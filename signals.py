"""Signal gating: turn raw per-company stances into emit-or-suppress decisions.

Rules:
  - Drop HOLD / neutral (no trade on neutral).
  - Drop below MIN_CONFIDENCE.
  - Per-ticker cooldown: suppress a repeat signal within TICKER_COOLDOWN unless
    the stance flipped (positive<->negative), which is itself news.
Surviving signals are persisted (SQLite + JSONL) and returned for notification.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config import cfg
from models import Signal
from store import Store


def _age_seconds(iso_ts: str) -> float:
    try:
        prev = datetime.fromisoformat(iso_ts)
    except ValueError:
        return 1e9
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - prev).total_seconds()


def gate(signals: list[Signal], store: Store) -> list[Signal]:
    emitted: list[Signal] = []
    for sig in signals:
        if sig.action == "HOLD" or sig.stance == "neutral":
            continue
        if sig.confidence < cfg.min_confidence:
            continue

        last = store.last_signal_for(sig.ticker)
        if last is not None:
            same_stance = last["stance"] == sig.stance
            # Compare against emission (wall-clock) time, not statement time, so
            # a burst of old/backfilled items can't each bypass the cooldown.
            within_cooldown = _age_seconds(last["emitted_at"]) < cfg.ticker_cooldown
            if same_stance and within_cooldown:
                # Duplicate / echo within cooldown — suppress.
                continue

        store.record_signal(sig, log_path=cfg.signals_log)
        emitted.append(sig)
    return emitted
