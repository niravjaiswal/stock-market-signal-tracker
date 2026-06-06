"""Tests for gating: threshold, neutral suppression, cooldown, stance-flip."""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from models import Signal
from signals import gate
from store import Store


def _store():
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return Store(tmp)


def _sig(ticker="BA", stance="negative", action="SHORT", conf=0.9):
    return Signal(
        utterance_id="u1", ticker=ticker, company="Boeing", stance=stance,
        action=action, confidence=conf, rationale="r", quote="q", source="demo",
    )


def test_below_threshold_suppressed():
    config.cfg.min_confidence = 0.6
    s = _store()
    assert gate([_sig(conf=0.4)], s) == []


def test_neutral_suppressed():
    s = _store()
    assert gate([_sig(stance="neutral", action="HOLD", conf=0.99)], s) == []


def test_emits_strong_signal():
    config.cfg.min_confidence = 0.6
    s = _store()
    out = gate([_sig(conf=0.9)], s)
    assert len(out) == 1 and out[0].action == "SHORT"


def test_cooldown_suppresses_duplicate():
    config.cfg.min_confidence = 0.6
    config.cfg.ticker_cooldown = 900
    s = _store()
    assert len(gate([_sig()], s)) == 1
    # Same ticker, same stance, immediately after -> suppressed.
    assert gate([_sig()], s) == []


def test_stance_flip_breaks_cooldown():
    config.cfg.min_confidence = 0.6
    config.cfg.ticker_cooldown = 900
    s = _store()
    assert len(gate([_sig(stance="negative", action="SHORT")], s)) == 1
    # Flip to positive within cooldown -> still emits (it's news).
    out = gate([_sig(stance="positive", action="BUY")], s)
    assert len(out) == 1 and out[0].action == "BUY"
