"""Core data structures passed between pipeline stages."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(text: str) -> str:
    """Lowercase + collapse whitespace + strip urls/punctuation noise.

    Used for cross-source dedup so the same quote echoed by Truth Social and
    five news outlets hashes to one value.
    """
    text = text.lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class Utterance:
    """One thing Trump said, from any source."""
    source: str                  # "truth_social" | "news" | "press_conf"
    text: str
    author: str = "Donald Trump"
    url: str = ""
    ts: datetime = field(default_factory=_now)
    raw: dict[str, Any] = field(default_factory=dict)
    # Stable per-source id (e.g. status id, article url, transcript slug+chunk).
    source_id: str = ""

    @property
    def id(self) -> str:
        """Globally unique id used for the utterances table primary key."""
        basis = f"{self.source}:{self.source_id or self.text}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def text_hash(self) -> str:
        """Content hash for cross-source echo dedup."""
        return hashlib.sha256(normalize_text(self.text).encode("utf-8")).hexdigest()[:16]


@dataclass
class CompanyMention:
    ticker: str
    name: str
    matched_text: str         # the surface form that matched ("Boeing", "Elon")
    context: str              # focused snippet around the mention
    source_pass: str = "regex"  # "regex" | "llm"


@dataclass
class Signal:
    utterance_id: str
    ticker: str
    company: str
    stance: str               # "positive" | "negative" | "neutral"
    action: str               # "BUY" | "SHORT" | "HOLD"
    confidence: float
    rationale: str
    quote: str                # the relevant snippet that drove the call
    source: str
    url: str = ""
    ts: datetime = field(default_factory=_now)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


STANCE_TO_ACTION = {"positive": "BUY", "negative": "SHORT", "neutral": "HOLD"}
