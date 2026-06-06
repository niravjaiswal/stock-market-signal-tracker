"""Source abstraction.

Each source polls some feed and returns a list of fresh Utterances. The
orchestrator handles scheduling, dedup, and the downstream pipeline, so a
source only needs to: fetch, parse, and return Utterances (newest-first is
fine; dedup happens centrally). Sources must never raise — return [] on error
so one flaky feed can't take the system down.
"""
from __future__ import annotations

import abc

import httpx

from models import Utterance

# A browser-ish UA helps with Cloudflare-fronted endpoints.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class Source(abc.ABC):
    name: str = "base"
    interval: int = 60  # seconds between polls

    def __init__(self, interval: int | None = None):
        if interval is not None:
            self.interval = interval

    @abc.abstractmethod
    async def fetch(self) -> list[Utterance]:
        """Return current batch of utterances. Must not raise."""
        raise NotImplementedError

    @staticmethod
    def client(timeout: float = 20.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html, */*"},
            follow_redirects=True,
        )
