"""Central configuration, loaded from environment / .env.

Everything tunable lives here so the rest of the code never reads os.environ
directly. Import `cfg` and read attributes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

# Load .env from the project root if present (no error if missing).
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


@dataclass
class Config:
    # LLM
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    model: str = field(default_factory=lambda: os.getenv("TRUMP_TRACKER_MODEL", "claude-haiku-4-5").strip())

    # Source toggles
    enable_truth_social: bool = field(default_factory=lambda: _bool("ENABLE_TRUTH_SOCIAL", True))
    enable_news: bool = field(default_factory=lambda: _bool("ENABLE_NEWS", True))
    enable_press_conf: bool = field(default_factory=lambda: _bool("ENABLE_PRESS_CONF", True))
    enable_live_transcription: bool = field(default_factory=lambda: _bool("ENABLE_LIVE_TRANSCRIPTION", False))

    # Intervals (seconds)
    truth_social_interval: int = field(default_factory=lambda: _int("TRUTH_SOCIAL_INTERVAL", 25))
    news_interval: int = field(default_factory=lambda: _int("NEWS_INTERVAL", 180))
    press_conf_interval: int = field(default_factory=lambda: _int("PRESS_CONF_INTERVAL", 120))

    # Signal tuning
    min_confidence: float = field(default_factory=lambda: _float("MIN_CONFIDENCE", 0.6))
    ticker_cooldown: int = field(default_factory=lambda: _int("TICKER_COOLDOWN", 900))

    # Notifications
    enable_desktop_notify: bool = field(default_factory=lambda: _bool("ENABLE_DESKTOP_NOTIFY", True))

    # Live transcription
    wh_youtube_live_url: str = field(
        default_factory=lambda: os.getenv("WH_YOUTUBE_LIVE_URL", "https://www.youtube.com/@WhiteHouse/live").strip()
    )

    # Paths
    root: Path = ROOT
    db_path: Path = ROOT / "data" / "tracker.db"
    signals_log: Path = ROOT / "data" / "signals.jsonl"
    tickers_path: Path = ROOT / "data" / "tickers.json"

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key)


cfg = Config()
