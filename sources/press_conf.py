"""Press-conference / remarks sources.

Two tiers (the orchestrator enables whichever are configured):

  PressConfSource (light, default)
    Polls the White House WordPress feed for newly published "Remarks" /
    "Press Briefing" transcripts and scans the full text. Near-real-time: WH
    posts official transcripts within minutes-to-hours of an event.

  LiveTranscriptionSource (heavy, opt-in: ENABLE_LIVE_TRANSCRIPTION=1)
    For coverage *during / immediately after* a live event. When the White
    House YouTube channel is live, it captures a short rolling audio segment
    (ffmpeg from the HLS edge) and transcribes it with faster-whisper. Requires
    `yt-dlp`, `faster-whisper`, and `ffmpeg` on PATH; degrades to [] if any are
    missing or nothing is live.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup

from config import cfg
from models import Utterance
from sources.base import Source

WH_FEED = "https://www.whitehouse.gov/feed/"
_REMARK_HINTS = ("remarks", "press", "briefing", "press conference", "press gaggle")


# ---------------------------------------------------------------------------
# Light: White House transcript feed
# ---------------------------------------------------------------------------
class PressConfSource(Source):
    name = "press_conf"

    async def fetch(self) -> list[Utterance]:
        try:
            async with self.client() as c:
                r = await c.get(WH_FEED)
                if r.status_code != 200:
                    return []
                content = r.content
        except Exception:
            return []
        try:
            feed = await asyncio.to_thread(feedparser.parse, content)
        except Exception:
            return []
        out: list[Utterance] = []
        for e in feed.entries[:15]:
            title = (e.get("title") or "").strip()
            low = title.lower()
            if not any(h in low for h in _REMARK_HINTS):
                continue
            # Only attribute to Trump when the title names him — the WH feed also
            # carries VP and press-secretary briefings, which are NOT Trump.
            if "trump" not in low:
                continue
            text = _entry_text(e)
            if len(text) < 80:  # skip stubs
                continue
            out.append(Utterance(
                source=self.name,
                source_id=e.get("link", title),
                text=text,
                author="Donald Trump",
                url=e.get("link", ""),
                ts=_struct_date(e),
                raw={"title": title, "via": "wh_feed"},
            ))
        return out


def _entry_text(entry) -> str:
    # WordPress feeds put the body in content[0].value or summary.
    html = ""
    if entry.get("content"):
        html = entry["content"][0].get("value", "")
    html = html or entry.get("summary", "")
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())


def _struct_date(entry) -> datetime:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        try:
            return datetime(*t[:6], tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Heavy: live YouTube transcription
# ---------------------------------------------------------------------------
class LiveTranscriptionSource(Source):
    name = "press_conf"  # same downstream label; it's still Trump at a podium

    def __init__(self, interval: int | None = None, segment_seconds: int = 45):
        super().__init__(interval)
        self.segment_seconds = segment_seconds
        self._model = None
        self._available = self._check_deps()

    @staticmethod
    def _check_deps() -> bool:
        if not (shutil.which("ffmpeg") and shutil.which("yt-dlp")):
            return False
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            # "base.en" is a good latency/accuracy balance on CPU.
            self._model = WhisperModel("base.en", device="cpu", compute_type="int8")
        return self._model

    async def fetch(self) -> list[Utterance]:
        if not self._available:
            return []
        hls = await self._live_hls_url()
        if not hls:
            return []
        wav = await self._capture(hls)
        if not wav:
            return []
        try:
            text = await asyncio.to_thread(self._transcribe, wav)
        finally:
            try:
                Path(wav).unlink(missing_ok=True)
            except Exception:
                pass
        text = text.strip()
        if len(text) < 25:
            return []
        # Dedup handled centrally by text_hash; segment id keeps it stable-ish.
        seg_id = hashlib.sha256(text.encode()).hexdigest()[:16]
        return [Utterance(
            source=self.name,
            source_id=f"live:{seg_id}",
            text=text,
            url=cfg.wh_youtube_live_url,
            ts=datetime.now(timezone.utc),
            raw={"via": "live_transcription"},
        )]

    async def _live_hls_url(self) -> str:
        """Resolve the live HLS manifest URL, or '' if not currently live."""
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-g", "-f", "bestaudio", cfg.wh_youtube_live_url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()  # reap, no zombie
            return ""
        if proc.returncode != 0:
            return ""
        return out.decode().strip().splitlines()[0] if out.strip() else ""

    async def _capture(self, hls_url: str) -> str:
        """Grab `segment_seconds` of audio from the live edge into a temp wav."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()

        def _cleanup() -> str:
            Path(tmp.name).unlink(missing_ok=True)
            return ""

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", hls_url, "-t", str(self.segment_seconds),
            "-ac", "1", "-ar", "16000", tmp.name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=self.segment_seconds + 30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()  # reap, no zombie
            return _cleanup()
        if proc.returncode != 0 or Path(tmp.name).stat().st_size < 1000:
            return _cleanup()
        return tmp.name

    def _transcribe(self, wav_path: str) -> str:
        model = self._load_model()
        segments, _ = model.transcribe(wav_path, language="en", vad_filter=True)
        return " ".join(s.text for s in segments)
