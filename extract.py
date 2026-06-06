"""Company detection: which tradeable public companies did Trump name?

Two passes for accuracy:
  1. Fast regex/alias pass over data/tickers.json (companies + CEO/person aliases).
  2. Optional Claude NER/disambiguation pass that catches companies not in the
     map, resolves them to tickers, and drops false positives (e.g. "apple" the
     fruit, "ford" the surname).
Results are merged and deduped by ticker.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Optional

from config import cfg
from llm import call_structured
from models import CompanyMention

_CONTEXT_RADIUS = 140


@lru_cache(maxsize=1)
def _alias_index() -> list[tuple[str, str, str]]:
    """Return list of (alias_lower, ticker, name), longest alias first so we
    match the most specific surface form."""
    data = json.loads(cfg.tickers_path.read_text(encoding="utf-8"))
    out: list[tuple[str, str, str]] = []
    for c in data["companies"]:
        ticker, name = c["ticker"], c["name"]
        forms = set(a.lower() for a in c["aliases"])
        forms.add(name.lower())
        for a in forms:
            out.append((a, ticker, name))
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return out


@lru_cache(maxsize=1)
def _ticker_names() -> dict[str, str]:
    data = json.loads(cfg.tickers_path.read_text(encoding="utf-8"))
    return {c["ticker"]: c["name"] for c in data["companies"]}


def _context(text: str, start: int, end: int) -> str:
    a = max(0, start - _CONTEXT_RADIUS)
    b = min(len(text), end + _CONTEXT_RADIUS)
    snippet = text[a:b].strip()
    if a > 0:
        snippet = "…" + snippet
    if b < len(text):
        snippet = snippet + "…"
    return snippet


def regex_extract(text: str) -> list[CompanyMention]:
    """Alias match with word boundaries. One mention per ticker (first hit)."""
    found: dict[str, CompanyMention] = {}
    for alias, ticker, name in _alias_index():
        if ticker in found:
            continue
        # \b doesn't play nice with symbols like & or . — guard with lookarounds.
        pattern = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            found[ticker] = CompanyMention(
                ticker=ticker,
                name=name,
                matched_text=m.group(0),
                context=_context(text, m.start(), m.end()),
                source_pass="regex",
            )
    return list(found.values())


_LLM_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Official company name"},
                    "ticker": {"type": "string", "description": "US stock ticker symbol, uppercase. Empty if not publicly traded / unknown."},
                    "matched_text": {"type": "string", "description": "The exact phrase in the text that refers to this company"},
                    "publicly_traded": {"type": "boolean"},
                },
                "required": ["name", "ticker", "matched_text", "publicly_traded"],
            },
        }
    },
    "required": ["companies"],
}

_LLM_EXTRACT_SYSTEM = (
    "You extract publicly traded companies that are actually being referred to as companies "
    "in a statement by Donald Trump. Include companies named directly, by brand, or via their "
    "well-known CEO/founder (e.g. Elon Musk -> Tesla). Resolve each to its primary US-listed "
    "ticker symbol. EXCLUDE: false positives where the word is not the company (e.g. 'apple' the "
    "fruit, a person's surname unrelated to the firm), private companies (set publicly_traded "
    "false), countries, and government agencies. Be precise; if unsure a word means the company, "
    "omit it."
)


def llm_extract(text: str, skip_tickers: set[str]) -> list[CompanyMention]:
    if not cfg.has_llm:
        return []
    result = call_structured(
        system=_LLM_EXTRACT_SYSTEM,
        user=f"Statement:\n\"\"\"\n{text}\n\"\"\"\n\nExtract the publicly traded companies referred to.",
        tool_name="report_companies",
        schema=_LLM_EXTRACT_SCHEMA,
        max_tokens=800,
    )
    if not result:
        return []
    names = _ticker_names()
    out: list[CompanyMention] = []
    for c in result.get("companies", []):
        ticker = (c.get("ticker") or "").strip().upper()
        if not c.get("publicly_traded") or not ticker or ticker in skip_tickers:
            continue
        matched = c.get("matched_text") or c.get("name", "")
        idx = text.lower().find(matched.lower()) if matched else -1
        ctx = _context(text, idx, idx + len(matched)) if idx >= 0 else text[:2 * _CONTEXT_RADIUS]
        out.append(CompanyMention(
            ticker=ticker,
            name=names.get(ticker, c.get("name", ticker)),
            matched_text=matched,
            context=ctx,
            source_pass="llm",
        ))
        skip_tickers.add(ticker)
    return out


def extract_companies(text: str, use_llm: bool = True) -> list[CompanyMention]:
    """Full two-pass extraction, deduped by ticker."""
    mentions = regex_extract(text)
    seen = {m.ticker for m in mentions}
    if use_llm and cfg.has_llm:
        mentions.extend(llm_extract(text, seen))
    return mentions
