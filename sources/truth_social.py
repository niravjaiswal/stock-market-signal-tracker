"""Truth Social source for @realDonaldTrump.

Primary: public Mastodon-compatible API
  https://truthsocial.com/api/v1/accounts/{id}/statuses
which is readable without auth for prominent accounts. If Cloudflare blocks it,
fall back to the public CNN archive JSON (refreshed ~every 5 minutes):
  https://ix.cnn.io/data/truth-social/truth_archive.json
"""
from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from models import Utterance
from sources.base import Source

TRUMP_ID = "107780257626128497"
API_URL = f"https://truthsocial.com/api/v1/accounts/{TRUMP_ID}/statuses"
ARCHIVE_URL = "https://ix.cnn.io/data/truth-social/truth_archive.json"


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return " ".join(text.split())


def _parse_ts(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


class TruthSocialSource(Source):
    name = "truth_social"

    def __init__(self, interval: int | None = None, limit: int = 20):
        super().__init__(interval)
        self.limit = limit

    async def fetch(self) -> list[Utterance]:
        utts = await self._fetch_api()
        if utts:
            return utts
        return await self._fetch_archive()

    async def _fetch_api(self) -> list[Utterance]:
        out: list[Utterance] = []
        try:
            async with self.client() as c:
                r = await c.get(API_URL, params={"limit": self.limit, "exclude_replies": "false"})
                if r.status_code != 200:
                    return []
                statuses = r.json()
        except Exception:
            return []
        for s in statuses or []:
            # Skip pure reblogs with no original content.
            content = s.get("content") or ""
            text = _strip_html(content)
            if not text and s.get("reblog"):
                rb = s["reblog"]
                text = _strip_html(rb.get("content", ""))
            if not text:
                continue
            out.append(Utterance(
                source=self.name,
                source_id=str(s.get("id", "")),
                text=text,
                url=s.get("url", ""),
                ts=_parse_ts(s.get("created_at", "")),
                raw={"id": s.get("id")},
            ))
        return out

    async def _fetch_archive(self) -> list[Utterance]:
        try:
            async with self.client() as c:
                r = await c.get(ARCHIVE_URL)
                if r.status_code != 200:
                    return []
                data = r.json()
        except Exception:
            return []
        # Archive is a list of post objects; schema is loose, handle common keys.
        items = data if isinstance(data, list) else data.get("posts", [])
        out: list[Utterance] = []
        for s in items[: self.limit]:
            text = _strip_html(s.get("text") or s.get("content") or "")
            if not text:
                continue
            out.append(Utterance(
                source=self.name,
                source_id=str(s.get("id") or s.get("url") or text[:40]),
                text=text,
                url=s.get("url", ""),
                ts=_parse_ts(s.get("created_at") or s.get("timestamp") or ""),
                raw={"via": "cnn_archive"},
            ))
        return out
