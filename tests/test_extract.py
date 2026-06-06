"""Tests for the regex/alias extraction pass (no network, no LLM key needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from extract import regex_extract


def _tickers(text):
    return {m.ticker for m in regex_extract(text)}


def test_direct_company_name():
    assert "BA" in _tickers("Boeing has done a terrible job.")


def test_ceo_alias_maps_to_company():
    assert "TSLA" in _tickers("Great call with Elon today.")
    assert "AMZN" in _tickers("Jeff Bezos and his Amazon scheme.")


def test_word_boundary_no_false_substring():
    # "fordham" should NOT match Ford.
    assert "F" not in _tickers("She studied at Fordham University.")


def test_brand_alias():
    assert "META" in _tickers("Facebook censored conservatives again.")
    assert "WBD" in _tickers("CNN is fake news.")


def test_multiple_companies_one_text():
    t = _tickers("Apple and Boeing both got huge contracts today.")
    assert {"AAPL", "BA"} <= t


def test_context_snippet_present():
    [m] = regex_extract("I love Tesla cars.")
    assert "Tesla" in m.context
    assert m.ticker == "TSLA"
