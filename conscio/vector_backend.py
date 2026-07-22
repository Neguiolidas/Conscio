"""VectorBackend — cosine vector store in SQLite BLOB float32.

Ported from MemPalace backends/sqlite_exact.py, simplified:
- BLOB serialization via array.array('f', vec).tobytes() (stdlib, no numpy needed)
- Cosine via numpy if available (fast), else math.fsum stdlib fallback
- Hostile review: rejects NaN input with ValueError

Schema:
    vectors(id TEXT PK, embedding BLOB, dimension INT, created_at TEXT)
"""
from __future__ import annotations
import array
import math
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Optional numpy for fast cosine
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_no_nan(vec: list[float]) -> None:
    """Hostile-review: reject NaN vectors."""
    for v in vec:
        if isinstance(v, float) and math.isnan(v):
            raise ValueError(f"Vector contains NaN at offset {vec.index(v)}")
        if v is None:
            raise ValueError(f"Vector contains None at offset {vec.index(v)}")


class VectorBackend:
    """SQLite-cosine vector store."""

    def __init__(self, db_path: str | Path | None = None, dimension: int = 768):
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "vec.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dimension = dimension
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
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    dimension INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Write ────────────────────────────────────────────────────────

    def add(self, id: str, vec: list[float]) -> None:
        """Insert or replace a vector."""
        _check_no_nan(vec)
        if len(vec) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
            )
        blob = array.array("f", vec).tobytes()
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO vectors (id, embedding, dimension) VALUES (?, ?, ?)",
                    (id, blob, self.dimension),
                )

    # ── Read ────────────────────────────────────────────────────────

    def _deserialize(self, blob: bytes) -> list[float]:
        arr = array.array("f")
        arr.frombytes(blob)
        return arr.tolist()

    def _cosine(self, a: list[float], b: list[float]) -> float:
        if np is not None:
            ar = np.frombuffer(array.array("f", a).tobytes(), dtype=np.float32)
            br = np.frombuffer(array.array("f", b).tobytes(), dtype=np.float32)
            na = np.linalg.norm(ar)
            if na == 0:
                return 0.0
            nb = np.linalg.norm(br)
            if nb == 0:
                return 0.0
            return float(np.dot(ar, br) / (na * nb))
        else:
            dot = math.fsum(a[i] * b[i] for i in range(self.dimension))
            na = math.sqrt(math.fsum(v * v for v in a))
            nb = math.sqrt(math.fsum(v * v for v in b))
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

    def search(self, query: list[float], limit: int = 5) -> list[dict]:
        """Cosine search: returns top-k match results."""
        _check_no_nan(query)
        if len(query) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(query)}"
            )
        with self._lock:
            conn = self._conn_get()
            rows = conn.execute(
                "SELECT id, embedding FROM vectors"
            ).fetchall()
        scored = []
        for row in rows:
            vec = self._deserialize(row["embedding"])
            score = self._cosine(query, vec)
            scored.append({"id": row["id"], "score": score})
        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]

    # ── Stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            conn = self._conn_get()
            cnt = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        return {"vectors": cnt, "dimension": self.dimension}

    def dump(self, target_path: str | Path) -> None:
        dst = sqlite3.connect(str(target_path))
        self._conn_get().backup(dst)
        dst.close()
