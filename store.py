"""SQLite persistence: dedup of utterances, mention log, and signal audit.

Single-threaded access from the async pipeline worker. Kept deliberately small;
the DB doubles as the dataset for future price-move backtesting.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import Signal, Utterance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS utterances (
    id          TEXT PRIMARY KEY,
    text_hash   TEXT,
    source      TEXT,
    source_id   TEXT,
    author      TEXT,
    text        TEXT,
    url         TEXT,
    ts          TEXT,
    seen_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_utt_texthash ON utterances(text_hash);

CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    utterance_id TEXT,
    ticker       TEXT,
    company      TEXT,
    stance       TEXT,
    action       TEXT,
    confidence   REAL,
    rationale    TEXT,
    quote        TEXT,
    source       TEXT,
    url          TEXT,
    ts           TEXT,
    emitted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sig_ticker_ts ON signals(ticker, ts);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # --- utterance dedup ---------------------------------------------------
    def count_utterances(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]

    def claim_utterance(self, utt: Utterance) -> bool:
        """Atomically claim an utterance for processing. Returns True if this is
        the first time we've seen it (by id) and it isn't an echo of content we
        already processed (by text_hash); False if it's a duplicate.

        Claiming = recording. The pipeline runs a single worker coroutine, so a
        claim-before-process flow is the simplest correct dedup: a duplicate can
        never slip through, at the cost of dropping an alert if processing then
        hard-fails (rare — analysis degrades to a fallback rather than raising).
        """
        if self.db.execute(
            "SELECT 1 FROM utterances WHERE id = ? OR text_hash = ? LIMIT 1",
            (utt.id, utt.text_hash),
        ).fetchone():
            return False
        self.db.execute(
            """INSERT OR IGNORE INTO utterances
               (id, text_hash, source, source_id, author, text, url, ts, seen_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                utt.id, utt.text_hash, utt.source, utt.source_id, utt.author,
                utt.text, utt.url, utt.ts.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.db.commit()
        return True

    # --- signal cooldown + audit ------------------------------------------
    def last_signal_for(self, ticker: str) -> Optional[sqlite3.Row]:
        cur = self.db.execute(
            "SELECT * FROM signals WHERE ticker = ? ORDER BY ts DESC LIMIT 1",
            (ticker,),
        )
        return cur.fetchone()

    def record_signal(self, sig: Signal, log_path: Optional[Path] = None) -> None:
        row = sig.to_row()
        # emitted_at is wall-clock emission time, used for cooldown — distinct
        # from `ts` (the statement time), so backfilled/old items don't bypass
        # the cooldown window.
        emitted_at = datetime.now(timezone.utc).isoformat()
        row["emitted_at"] = emitted_at
        self.db.execute(
            """INSERT INTO signals
               (utterance_id, ticker, company, stance, action, confidence,
                rationale, quote, source, url, ts, emitted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["utterance_id"], row["ticker"], row["company"], row["stance"],
                row["action"], row["confidence"], row["rationale"], row["quote"],
                row["source"], row["url"], row["ts"], emitted_at,
            ),
        )
        self.db.commit()
        # JSONL is a best-effort audit log; a file error must not suppress the
        # alert (the DB row is the source of truth, the notification is next).
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            except OSError as e:
                print(f"[store] signals.jsonl write failed (non-fatal): {e}")

    def close(self) -> None:
        self.db.close()
