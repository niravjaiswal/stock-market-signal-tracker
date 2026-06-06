"""Stance analysis: is Trump positive or negative about each named company?

Primary path is a single Claude call covering every company in the utterance,
returning per-company stance + confidence + rationale. If no API key is set, a
small finance-sentiment lexicon fallback keeps the pipeline alive (lower
confidence, capped so it won't trip aggressive thresholds blindly).
"""
from __future__ import annotations

import re
from typing import Optional

from config import cfg
from llm import call_structured
from models import STANCE_TO_ACTION, CompanyMention, Signal, Utterance

# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

_STANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "stance": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "confidence": {"type": "number", "description": "0..1 how confident the stance is, given how clearly Trump praises/attacks the company's prospects"},
                    "rationale": {"type": "string", "description": "one short sentence"},
                    "quote": {"type": "string", "description": "the exact phrase showing the stance"},
                },
                "required": ["ticker", "stance", "confidence", "rationale", "quote"],
            },
        }
    },
    "required": ["results"],
}

_STANCE_SYSTEM = (
    "You judge how Donald Trump feels about specific publicly traded companies in a statement, "
    "for a trading-signal tool. For each company given, decide stance: 'positive' (he praises it, "
    "boosts it, gives it a deal/contract, defends it), 'negative' (he attacks, threatens, blames, "
    "mocks, or wants to punish/regulate/tariff it), or 'neutral' (mentioned without clear "
    "directional sentiment about the company's prospects). Confidence reflects how clearly the "
    "language would move that stock. Praise of a CEO personally still counts for that CEO's "
    "company. Be conservative: when ambiguous, use 'neutral'."
)


def _llm_analyze(utt: Utterance, mentions: list[CompanyMention]) -> Optional[list[Signal]]:
    companies = ", ".join(f"{m.ticker} ({m.name})" for m in mentions)
    by_ticker = {m.ticker: m for m in mentions}
    user = (
        f"Statement by Trump:\n\"\"\"\n{utt.text}\n\"\"\"\n\n"
        f"Companies to assess: {companies}\n\n"
        "Return one result per company."
    )
    result = call_structured(
        system=_STANCE_SYSTEM,
        user=user,
        tool_name="report_stances",
        schema=_STANCE_SCHEMA,
        max_tokens=1200,
    )
    if not result:
        return None
    signals: list[Signal] = []
    for r in result.get("results", []):
        ticker = (r.get("ticker") or "").strip().upper()
        m = by_ticker.get(ticker)
        if m is None:
            continue
        stance = r.get("stance", "neutral")
        signals.append(Signal(
            utterance_id=utt.id,
            ticker=ticker,
            company=m.name,
            stance=stance,
            action=STANCE_TO_ACTION.get(stance, "HOLD"),
            confidence=float(r.get("confidence", 0.0)),
            rationale=r.get("rationale", ""),
            quote=r.get("quote", "") or m.context,
            source=utt.source,
            url=utt.url,
            ts=utt.ts,
        ))
    return signals


# ---------------------------------------------------------------------------
# Lexicon fallback (no API key)
# ---------------------------------------------------------------------------

_POS = {
    "great", "incredible", "amazing", "tremendous", "fantastic", "best", "strong",
    "winning", "boom", "booming", "thank", "thriving", "love", "beautiful", "respect",
    "deal", "invest", "investing", "billions", "jobs", "back", "comeback",
}
_NEG = {
    "terrible", "disaster", "failing", "fake", "corrupt", "bad", "worst", "weak",
    "fraud", "scam", "rip", "ripping", "tariff", "tariffs", "sue", "boycott", "fire",
    "fired", "crooked", "disgrace", "horrible", "pathetic", "lost", "losing", "kill",
    "killing", "ban", "banned", "overrated", "nasty",
}


def _lexicon_analyze(utt: Utterance, mentions: list[CompanyMention]) -> list[Signal]:
    signals: list[Signal] = []
    for m in mentions:
        words = set(re.findall(r"[a-z']+", m.context.lower()))
        pos = len(words & _POS)
        neg = len(words & _NEG)
        delta = abs(pos - neg)
        if pos == neg:
            stance, conf = "neutral", 0.3
        elif pos > neg:
            stance, conf = "positive", min(0.7, 0.4 + 0.1 * delta)
        else:
            stance, conf = "negative", min(0.7, 0.4 + 0.1 * delta)
        signals.append(Signal(
            utterance_id=utt.id,
            ticker=m.ticker,
            company=m.name,
            stance=stance,
            action=STANCE_TO_ACTION[stance],
            confidence=conf,
            rationale="(lexicon fallback — no LLM key set)",
            quote=m.context,
            source=utt.source,
            url=utt.url,
            ts=utt.ts,
        ))
    return signals


def analyze(utt: Utterance, mentions: list[CompanyMention]) -> list[Signal]:
    """Return a Signal per mention.

    With an API key, stance comes from Claude; if that call fails, we skip this
    utterance (return []) rather than firing a crude keyword-lexicon alert that
    could be wrong. The lexicon path is only for running with no key at all.
    """
    if not mentions:
        return []
    if cfg.has_llm:
        out = _llm_analyze(utt, mentions)
        return out if out is not None else []
    return _lexicon_analyze(utt, mentions)
