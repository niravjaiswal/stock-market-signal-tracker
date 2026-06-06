# Trump Market-Signal Tracker

Real-time tracker that listens to what Donald Trump says — on **Truth Social**, in
**the news**, and at **press conferences** — detects any **publicly traded company**
he mentions, uses **Claude** to judge whether he's positive or negative about it, and
fires a **BUY / SHORT** alert to your terminal and macOS desktop.

> ⚠️ **NOT FINANCIAL ADVICE.** This is a personal research tool operating only on
> Trump's *public* statements. It is **alert-only** — it never places trades. Markets
> are noisy and reflexive; treat every signal as a prompt to look, not to act.

## How it works

```
            ┌──────────── sources (poll on own interval) ────────────┐
 Truth Social API ─┐   GDELT + RSS news ─┐   WH transcript feed ─┐   (opt) live YouTube
   + CNN archive   │                     │   + Factbase          │    → ffmpeg → Whisper
                   └──────────┬──────────┴──────────┬────────────┘
                              ▼  async queue (cross-source dedup)
                   extract ──► analyze ──► gate ──► notify
                   (regex +    (Claude     (conf/    (terminal +
                    Claude NER) stance)    cooldown)  desktop)
```

1. **Sources** each poll their feed and push new utterances onto a shared queue.
   They never raise — a flaky feed just yields nothing that cycle.
2. **Extract** (`extract.py`): a fast alias/regex pass over `data/tickers.json`
   (companies **and** the CEO/person names Trump uses — Elon→TSLA, Bezos→AMZN…),
   then a Claude NER pass to catch anything missing and drop false positives
   (the fruit "apple", the surname "Ford").
3. **Analyze** (`analyze.py`): one Claude call rates each company
   positive / negative / neutral with a confidence and one-line rationale.
   No API key? A keyword-lexicon fallback keeps it running at lower confidence.
4. **Gate** (`signals.py`): drop neutral, drop below `MIN_CONFIDENCE`, and apply a
   per-ticker cooldown (unless the stance flips). Cross-source text-hash dedup means
   the same quote echoed by Truth Social + five outlets fires **once**.
5. **Notify** (`notify.py`): colored terminal panel + macOS notification.

Everything is logged to SQLite (`data/tracker.db`) and `data/signals.jsonl` for
later price-move backtesting.

## Setup

```bash
cd trump_tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your ANTHROPIC_API_KEY into .env
```

Optional (only for live press-conference transcription):

```bash
pip install yt-dlp faster-whisper
brew install ffmpeg
# then set ENABLE_LIVE_TRANSCRIPTION=1 in .env
```

## Run

```bash
python main.py --demo        # offline sanity check: canned quotes → signals
python main.py --backfill    # pull recent real posts/news once, print signals
python main.py               # live: poll forever, alert in real time
python main.py --source truth_social   # restrict to one source
python -m pytest tests/      # unit tests (no network/key needed)
```

`--demo` expects, with a key set: **SHORT BA**, **BUY AAPL**, **SHORT NYT**, **BUY TSLA**
plus four matching desktop notifications.

**First live run primes silently.** On an empty DB, `python main.py` records the current
batch of posts/headlines *without* alerting, so you don't get a flood of signals for old
statements. Only genuinely new statements after that point fire alerts. (`--once` /
`--backfill` skip priming — they intentionally process and alert on the recent batch.)

## Configuration (`.env`)

| Var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables Claude extraction + stance. Empty → lexicon fallback. |
| `TRUMP_TRACKER_MODEL` | `claude-haiku-4-5` | Claude model id. Haiku = cheapest (~$10–18/mo running 24/7); set `claude-opus-4-8` for max accuracy (~$30–90/mo). |
| `ENABLE_TRUTH_SOCIAL` / `ENABLE_NEWS` / `ENABLE_PRESS_CONF` | `1` | Source toggles. |
| `ENABLE_LIVE_TRANSCRIPTION` | `0` | Live YouTube→Whisper (needs yt-dlp+faster-whisper+ffmpeg). |
| `TRUTH_SOCIAL_INTERVAL` / `NEWS_INTERVAL` / `PRESS_CONF_INTERVAL` | `25` / `180` / `120` | Poll seconds. |
| `MIN_CONFIDENCE` | `0.6` | Min LLM confidence to emit a signal. |
| `TICKER_COOLDOWN` | `900` | Seconds to suppress repeat signals per ticker. |
| `ENABLE_DESKTOP_NOTIFY` | `1` | macOS desktop notifications on/off. |

## Data sources & resilience

- **Truth Social**: public Mastodon API for `@realDonaldTrump`; auto-falls back to
  the public CNN archive JSON if Cloudflare blocks the API.
- **News**: GDELT DOC 2.0 (free, ~real-time) + Reuters/CNN/Fox politics RSS.
- **Press conferences**: White House WordPress transcript feed (light, default);
  optional live YouTube transcription for during/immediately-after coverage.

All endpoints are unofficial and may change; each source is isolated behind a
common interface with a fallback, so one breaking won't take down the system.

## Adding companies

Edit `data/tickers.json` — add `{ "ticker", "name", "aliases": [...] }`. Include the
brands and the CEO/founder names Trump is likely to say. The Claude pass will still
catch tickers you forget.

## Roadmap

- Price-move backtesting from the signal log (data already captured).
- Optional broker paper-trading hook (Alpaca) — deliberately omitted; alert-only.
- Sub-minute press-conf latency via continuous live capture instead of polling.
