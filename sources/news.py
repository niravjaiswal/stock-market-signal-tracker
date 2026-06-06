"""News source: near-real-time articles where Trump comments on something.

Two feeds, both free / no key:
  - GDELT DOC 2.0 API — global news index, queried for recent Trump-quote
    articles, refreshed every ~15 min with a rolling timespan.
  - A few politics RSS feeds for redundancy / lower latency on big outlets.

News text is a headline/snippet (a paraphrase of what Trump said), not his exact
words. The downstream LLM still resolves the company + stance; cross-source
text-hash dedup collapses the same story reported by many outlets into one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import feedparser

from models import Utterance
from sources.base import Source

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# Bias the query toward Trump *saying* something directional about a company.
GDELT_QUERY = '(Trump) (says OR said OR slams OR praises OR threatens OR tariff OR boycott OR endorses)'

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/politicsNews",
    "https://rss.cnn.com/rss/cnn_allpolitics.rss",
    "https://moxie.foxnews.com/google-publisher/politics.xml",
]

# Only keep items that look like Trump is the speaker.
_SPEAKER_HINTS = ("trump", "president")


class NewsSource(Source):
    name = "news"

    def __init__(self, interval: int | None = None, max_records: int = 50):
        super().__init__(interval)
        self.max_records = max_records

    async def fetch(self) -> list[Utterance]:
        gdelt, rss = await asyncio.gather(self._gdelt(), self._rss())
        return gdelt + rss

    async def _gdelt(self) -> list[Utterance]:
        params = {
            "query": GDELT_QUERY,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(self.max_records),
            "timespan": "30min",
            "sort": "datedesc",
        }
        try:
            async with self.client() as c:
                r = await c.get(GDELT_URL, params=params)
                if r.status_code != 200:
                    return []
                data = r.json()
        except Exception:
            return []
        out: list[Utterance] = []
        for a in data.get("articles", []):
            title = (a.get("title") or "").strip()
            if not title or not any(h in title.lower() for h in _SPEAKER_HINTS):
                continue
            out.append(Utterance(
                source=self.name,
                source_id=a.get("url", title),
                text=title,
                url=a.get("url", ""),
                ts=_gdelt_date(a.get("seendate", "")),
                raw={"domain": a.get("domain", ""), "via": "gdelt"},
            ))
        return out

    async def _rss(self) -> list[Utterance]:
        results = await asyncio.gather(*(self._one_feed(u) for u in RSS_FEEDS))
        out: list[Utterance] = []
        for batch in results:
            out.extend(batch)
        return out

    async def _one_feed(self, url: str) -> list[Utterance]:
        try:
            async with self.client() as c:
                r = await c.get(url)
                if r.status_code != 200:
                    return []
                content = r.content
        except Exception:
            return []
        # feedparser is sync/CPU-light; parse off the event loop. Guard it — a
        # malformed feed must not escape gather() and kill the whole news fetch.
        try:
            feed = await asyncio.to_thread(feedparser.parse, content)
        except Exception:
            return []
        out: list[Utterance] = []
        for e in feed.entries[: self.max_records]:
            title = (e.get("title") or "").strip()
            summary = (e.get("summary") or "").strip()
            text = title
            if not text or not any(h in (title + " " + summary).lower() for h in _SPEAKER_HINTS):
                continue
            out.append(Utterance(
                source=self.name,
                source_id=e.get("link", title),
                text=text,
                url=e.get("link", ""),
                ts=_struct_date(e),
                raw={"via": "rss"},
            ))
        return out


def _gdelt_date(value: str) -> datetime:
    # GDELT seendate format: 20260605T143000Z
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _struct_date(entry) -> datetime:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        try:
            return datetime(*t[:6], tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)
