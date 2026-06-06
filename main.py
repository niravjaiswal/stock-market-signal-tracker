"""Trump market-signal tracker — orchestrator.

Real-time loop: each enabled source polls on its own interval and pushes new
utterances onto a shared queue; one pipeline worker pulls each utterance through
extract -> analyze -> gate -> notify.

Usage:
  python main.py                     # live loop, all enabled sources
  python main.py --once              # one poll cycle per source, then exit
  python main.py --backfill          # pull recent history once, then exit
  python main.py --source truth_social   # restrict to one source (repeatable)
  python main.py --demo              # offline: run canned quotes through pipeline

NOT FINANCIAL ADVICE. Personal research tool over Trump's *public* statements.
"""
from __future__ import annotations

import argparse
import asyncio
import signal as _signal
import sys
from pathlib import Path

from analyze import analyze
from config import cfg
from extract import extract_companies
from models import Utterance
from notify import banner, console, emit, log_utterance
from signals import gate
from store import Store

# ---------------------------------------------------------------------------
# Pipeline (shared by every mode)
# ---------------------------------------------------------------------------
async def process(utt: Utterance, store: Store, use_llm: bool = True) -> int:
    """Run one utterance through the full pipeline. Returns # signals emitted."""
    if not store.claim_utterance(utt):  # atomic dedup (id + cross-source text)
        return 0
    log_utterance(utt.source, utt.text)

    mentions = await asyncio.to_thread(extract_companies, utt.text, use_llm)
    if not mentions:
        return 0
    console.print(
        f"[dim]  ↳ companies: {', '.join(m.ticker for m in mentions)}[/dim]"
    )
    raw_signals = await asyncio.to_thread(analyze, utt, mentions)
    emitted = gate(raw_signals, store)
    for sig in emitted:
        emit(sig)
    return len(emitted)


# ---------------------------------------------------------------------------
# Source wiring
# ---------------------------------------------------------------------------
def build_sources(only: set[str] | None):
    from sources.news import NewsSource
    from sources.press_conf import LiveTranscriptionSource, PressConfSource
    from sources.truth_social import TruthSocialSource

    srcs = []
    if cfg.enable_truth_social and _want("truth_social", only):
        srcs.append(TruthSocialSource(interval=cfg.truth_social_interval))
    if cfg.enable_news and _want("news", only):
        srcs.append(NewsSource(interval=cfg.news_interval))
    if cfg.enable_press_conf and _want("press_conf", only):
        srcs.append(PressConfSource(interval=cfg.press_conf_interval))
    if cfg.enable_live_transcription and _want("press_conf", only):
        srcs.append(LiveTranscriptionSource(interval=max(30, cfg.press_conf_interval)))
    return srcs


def _want(name: str, only: set[str] | None) -> bool:
    return only is None or name in only


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------
async def run_once(only, store) -> None:
    srcs = build_sources(only)
    if not srcs:
        console.print("[yellow]No sources enabled. Check .env toggles.[/yellow]")
        return
    for src in srcs:
        console.print(f"[cyan]Polling {src.name}…[/cyan]")
        utts = await src.fetch()
        # Oldest-first so cooldown logic sees statements in chronological order.
        utts = sorted(utts, key=lambda u: u.ts)
        total = 0
        for utt in utts:
            total += await process(utt, store)
        console.print(f"[dim]  {src.name}: {len(utts)} utterances, {total} signals[/dim]")


async def run_live(only, store) -> None:
    srcs = build_sources(only)
    if not srcs:
        console.print("[yellow]No sources enabled. Check .env toggles.[/yellow]")
        return

    # First-run priming: an empty DB means every current post/headline looks
    # "new" and would fire a burst of alerts for old statements. Record the
    # current batch as seen without alerting, so only genuinely NEW statements
    # from here on trigger signals.
    if store.count_utterances() == 0:
        console.print("[cyan]First run — priming (recording current items, no alerts)…[/cyan]")
        for src in srcs:
            try:
                for utt in await src.fetch():
                    store.claim_utterance(utt)
            except Exception as e:
                console.print(f"[red]{src.name} prime error: {e}[/red]")
        console.print(
            f"[dim]Primed {store.count_utterances()} items. Live — only NEW statements alert.[/dim]"
        )

    queue: asyncio.Queue[Utterance] = asyncio.Queue()
    stop = asyncio.Event()

    async def poller(src):
        while not stop.is_set():
            try:
                for utt in sorted(await src.fetch(), key=lambda u: u.ts):
                    await queue.put(utt)
            except Exception as e:
                console.print(f"[red]{src.name} poll error: {e}[/red]")
            try:
                await asyncio.wait_for(stop.wait(), timeout=src.interval)
            except asyncio.TimeoutError:
                pass

    async def worker():
        while not stop.is_set():
            try:
                utt = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await process(utt, store)
            except Exception as e:
                console.print(f"[red]pipeline error: {e}[/red]")
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(poller(s)) for s in srcs]
    tasks.append(asyncio.create_task(worker()))

    loop = asyncio.get_running_loop()
    for s in (_signal.SIGINT, _signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass

    console.print(
        f"[green]Live. Watching: {', '.join(s.name for s in srcs)}. Ctrl-C to stop.[/green]"
    )
    await stop.wait()
    console.print("\n[yellow]Shutting down…[/yellow]")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


DEMO_QUOTES = [
    "Boeing has done a really terrible job on Air Force One. The costs are out of "
    "control, a total disaster. I may cancel the whole order!",
    "I want to thank Tim Cook and Apple for investing BILLIONS of dollars in the "
    "United States. Tremendous company, creating tens of thousands of jobs!",
    "The failing New York Times wrote another FAKE story. Nobody reads them anymore, "
    "a total disgrace to journalism.",
    "Had a great call with Elon. Tesla is doing incredible things for American "
    "manufacturing. We love what they're building!",
]


async def run_demo(store) -> None:
    banner(
        "DEMO MODE — feeding canned Trump quotes through the full pipeline.\n"
        + ("LLM stance analysis ON." if cfg.has_llm else
           "No ANTHROPIC_API_KEY — using keyword lexicon fallback (lower confidence).")
    )
    from datetime import datetime, timezone
    for i, q in enumerate(DEMO_QUOTES):
        utt = Utterance(
            source="demo",
            source_id=f"demo-{i}",
            text=q,
            url="https://example.com/demo",
            ts=datetime.now(timezone.utc),
        )
        await process(utt, store)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Trump market-signal tracker")
    p.add_argument("--once", action="store_true", help="one poll cycle then exit (no priming — will alert on current items)")
    p.add_argument("--backfill", action="store_true", help="alias for --once: process the recent batch and emit signals, then exit")
    p.add_argument("--demo", action="store_true", help="offline canned-quote pipeline test")
    p.add_argument("--source", action="append", default=None,
                   help="restrict to a source (truth_social|news|press_conf); repeatable")
    return p.parse_args(argv)


async def amain(argv) -> None:
    args = parse_args(argv)
    only = set(args.source) if args.source else None
    if args.demo:
        # Fresh throwaway DB so the demo always re-runs cleanly (dedup is global).
        import tempfile
        store = Store(Path(tempfile.mkdtemp()) / "demo.db")
    else:
        store = Store(cfg.db_path)
    banner(
        "[bold]Trump Market-Signal Tracker[/bold]\n"
        "[red]NOT FINANCIAL ADVICE[/red] · alert-only · public statements\n"
        f"LLM: {'Claude ' + cfg.model if cfg.has_llm else 'lexicon fallback (no key)'} · "
        f"min-confidence {cfg.min_confidence:.0%} · cooldown {cfg.ticker_cooldown}s"
    )
    try:
        if args.demo:
            await run_demo(store)
        elif args.once or args.backfill:
            await run_once(only, store)
        else:
            await run_live(only, store)
    finally:
        store.close()


def main():
    try:
        asyncio.run(amain(sys.argv[1:]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
