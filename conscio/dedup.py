"""Deduplicator — hash + Jaccard bigram similarity.

URL: ported concept from MemPalace dedup.py, simplified for Conscio.

- Hash: SHA256 of NFC-normalized lowercase text (handles Unicode accents)
- Similarity: Jaccard on bigrams (stdlib only)
- Persistence: dedup_registry table

Schema:
    dedup_registry(hash TEXT PK, content TEXT, registered_at TEXT)
"""
from __future__ import annotations
import hashlib
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(text: str) -> str:
    """NFKD + ASCII fold + lowercase: removes accents á→a, ç→c."""
    nfkd = unicodedata.normalize("NFKD", text)
    # Remove combining marks (dáStrip accents)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_only.lower().strip()


class Deduplicator:
    """Hash + Jaccard similarity dedup."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        similarity_threshold: float = 0.85,
    ):
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "dedup.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._threshold = similarity_threshold
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _conn_get(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn_get()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dedup_registry (
                    hash TEXT PRIMARY KEY,
                    content TEXT,
                    registered_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Hash ────────────────────────────────────────────────────────

    def compute_hash(self, content: str) -> str:
        """SHA256 of NFC-normalized lowercase content."""
        normalized = _normalize(content)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # ── Exact dedup (by hash) ───────────────────────────────────────

    def register(self, hash: str, content: str) -> None:
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO dedup_registry (hash, content) VALUES (?, ?)",
                    (hash, content),
                )

    def is_duplicate(self, hash: str) -> bool:
        with self._lock:
            conn = self._conn_get()
            row = conn.execute("SELECT 1 FROM dedup_registry WHERE hash = ?", (hash,)).fetchone()
        return row is not None

    # ── Near-duplicate (Jaccard bigrams) ───────────────────────────

    @staticmethod
    def _bigrams(text: str) -> set[str]:
        """Tokenize text into bigrams (2-char windows)."""
        normalized = _normalize(text)
        # Remove word boundaries punctuation
        tokens = re.findall(r"\w+", normalized)
        bigrams: set[str] = set()
        for tok in tokens:
            if len(tok) < 2:
                bigrams.add(tok)
            else:
                for i in range(len(tok) - 1):
                    bigrams.add(tok[i + 0 : i + 2])
        return bigrams

    def is_near_duplicate(self, t1: str, t2: str) -> bool:
        """Check if two texts are near-duplicates via Jaccard on bigrams."""
        b1 = self._bigrams(t1)
        b2 = self._bigrams(t2)
        if not b1 or not b2:
            return False
        inter = b1 & b2
        union = b1 | b2
        jaccard = len(inter) / len(union) if union else 0
        return jaccard >= self._threshold

    # ── Stats ──────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            conn = self._conn_get()
            cnt = conn.execute("SELECT COUNT(*) FROM dedup_registry").fetchone()[0]
        return {"total": cnt}

    def dump(self, target_path: str | Path) -> None:
        dst = sqlite3.connect(str(target_path))
        self._conn_get().backup(dst)
        dst.close()
