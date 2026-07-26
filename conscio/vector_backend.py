"""VectorBackend — cosine vector store in SQLite BLOB float32.

Ported from MemPalace backends/sqlite_exact.py, simplified:
- BLOB serialization via array.array('f', vec).tobytes() (stdlib, no numpy needed)
- Cosine via numpy if available (fast), else math.fsum stdlib fallback
- Hostile review: rejects NaN input with ValueError

Schema:
    vectors(id TEXT PK, embedding BLOB, dimension INT, category TEXT,
            created_at TEXT)

`category` mirrors `chunks.source_category` for the chunk a vector was built
from. It exists purely so a category-scoped recall can restrict the *candidate
set in SQL* instead of cosine-scoring the whole index and throwing away 99% of
the work afterwards (see `search(category=...)`). Rows written before this
column existed carry NULL and are still scanned when a category is requested —
they are then filtered by the caller against the real chunk row, so an old
index degrades to the previous behavior instead of silently returning nothing.

Scoring is a single vectorized numpy pass over a contiguous float32 buffer per
batch of rows (`np.frombuffer` + matrix-vector product), never a per-row Python
loop: search() sits in the hot path of every recall() call, over an index that
is expected to hold ~200k chunk vectors.
"""
from __future__ import annotations

import array
import heapq
import logging
import math
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

# Optional numpy for fast cosine
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)

# Rows pulled (and scored) per vectorized pass. Bounds peak memory to
# ~batch * dimension * 4 bytes (4096 * 768 * 4 ≈ 12MB) regardless of index size,
# so a full scan never materializes the whole index in RAM.
_SCAN_BATCH = 4096


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_no_nan(vec: Sequence[float]) -> None:
    """Hostile-review: reject NaN/None vectors.

    numpy path is O(n) in C — the stdlib path is only a fallback. (The previous
    implementation called list.index() to build the error message, which is
    O(n) *per element* on the error path and, for NaN, relies on identity.)
    """
    if np is not None:
        try:
            arr = np.asarray(vec, dtype=np.float64)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Vector contains a non-numeric value (None?): {e}") from e
        if arr.size:
            bad = np.isnan(arr)
            if bool(bad.any()):
                raise ValueError(f"Vector contains NaN at offset {int(np.argmax(bad))}")
        return
    for i, v in enumerate(vec):
        if v is None:
            raise ValueError(f"Vector contains None at offset {i}")
        if isinstance(v, float) and math.isnan(v):
            raise ValueError(f"Vector contains NaN at offset {i}")


class VectorBackend:
    """SQLite-cosine vector store."""

    def __init__(self, db_path: str | Path | None = None, dimension: int = 768):
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "vec.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dimension = dimension
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
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
            # Migration: `category` was added in v3.6 so category-scoped recall
            # can pre-filter candidates in SQL. Existing DBs get the column
            # (NULL for every old row) instead of being rebuilt.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(vectors)")}
            if "category" not in cols:
                conn.execute("ALTER TABLE vectors ADD COLUMN category TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vectors_category ON vectors(category)"
            )
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Write ────────────────────────────────────────────────────────

    def _row_for(self, id: str, vec: Sequence[float], category: str | None) -> tuple:
        """Validate one vector and build its INSERT tuple."""
        _check_no_nan(vec)
        if len(vec) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
            )
        return (id, array.array("f", vec).tobytes(), self.dimension, category)

    def add(self, id: str, vec: list[float], category: str | None = None) -> None:
        """Insert or replace a single vector (one transaction)."""
        row = self._row_for(id, vec, category)
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO vectors (id, embedding, dimension, category)"
                    " VALUES (?, ?, ?, ?)",
                    row,
                )

    def add_batch(
        self,
        items: Iterable[tuple[str, Sequence[float]]],
        category: str | None = None,
    ) -> int:
        """Insert or replace many vectors in ONE transaction.

        The per-vector `add()` pays a full commit (fsync + WAL frame) per chunk,
        which dominates ingest wall-clock once a document produces hundreds of
        chunks. Every vector is validated *before* anything is written, so a bad
        vector aborts the batch instead of leaving it half-applied.

        Returns the number of vectors written.
        """
        rows = [self._row_for(id_, vec, category) for id_, vec in items]
        if not rows:
            return 0
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO vectors (id, embedding, dimension, category)"
                    " VALUES (?, ?, ?, ?)",
                    rows,
                )
        return len(rows)

    def ensure_dimension(self, dim: int) -> bool:
        """Reconcile the configured dimension with what the embedder produces.

        The configured dimension is a *guess* derived from env vars; the model
        actually loaded may disagree (e.g. an Ollama model that returns 1024).
        When the store is still empty there is nothing to be consistent with, so
        adopt the real dimension instead of rejecting every single write.
        Otherwise report the conflict (False) and let the caller decide.
        """
        if dim == self.dimension:
            return True
        with self._lock:
            conn = self._conn_get()
            occupied = conn.execute("SELECT 1 FROM vectors LIMIT 1").fetchone() is not None
        if occupied:
            return False
        logger.info(
            "VectorBackend: adopting embedder dimension %d (was %d, store empty)",
            dim, self.dimension,
        )
        self.dimension = dim
        return True

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

    def search(
        self,
        query: list[float],
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict]:
        """Cosine search: returns the top-`limit` matches, best first.

        Hot path — every recall() lands here against an index expected to hold
        ~200k vectors, so this must never be a per-row Python loop:

        * rows are streamed in batches of `_SCAN_BATCH` and scored with ONE
          numpy matrix-vector product per batch over a contiguous float32
          buffer (`np.frombuffer` on the joined blobs — no per-row deserialize,
          no per-row `array` object);
        * peak memory stays at batch * dimension * 4 bytes, independent of how
          big the index gets;
        * `category` restricts the candidate set in SQL, so a scoped recall
          scores only its own slice instead of the whole index (rows written
          before the column existed are NULL and stay in the candidate set —
          the caller filters those against the real chunk row, so an old index
          degrades to the old behavior rather than returning nothing);
        * only the running top-k is retained (heap), not a score per row.

        The stdlib fallback keeps the old row-at-a-time math when numpy is
        absent; it is correctness-only, not a performance path.
        """
        _check_no_nan(query)
        if len(query) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(query)}"
            )
        if limit <= 0:
            return []

        sql = "SELECT id, embedding FROM vectors"
        params: tuple = ()
        if category is not None:
            # NULL = written before the category column existed; keep it as a
            # candidate rather than making old indexes look empty.
            sql += " WHERE category = ? OR category IS NULL"
            params = (category,)

        expected_bytes = self.dimension * 4
        qnorm_arr = None
        qnorm = 0.0
        if np is not None:
            qnorm_arr = np.asarray(query, dtype=np.float32)
            qnorm = float(np.linalg.norm(qnorm_arr))
            if qnorm == 0.0:
                return []

        # (score, id) heap of size <= limit; ties break on id, deterministically.
        heap: list[tuple[float, str]] = []
        skipped = 0

        with self._lock:
            conn = self._conn_get()
            cur = conn.execute(sql, params)
            while True:
                rows = cur.fetchmany(_SCAN_BATCH)
                if not rows:
                    break

                ids: list[str] = []
                blobs: list[bytes] = []
                for row in rows:
                    blob = row["embedding"]
                    if len(blob) != expected_bytes:
                        # Stale row from a different embedding model/dimension.
                        skipped += 1
                        continue
                    ids.append(row["id"])
                    blobs.append(blob)
                if not ids:
                    continue

                # qnorm_arr is set iff numpy is available; testing both keeps
                # the narrowing explicit for type checkers.
                if np is not None and qnorm_arr is not None:
                    mat = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(
                        len(ids), self.dimension
                    )
                    dots = mat @ qnorm_arr                      # one BLAS call
                    norms = np.linalg.norm(mat, axis=1)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        scores = np.where(norms > 0, dots / (norms * qnorm), 0.0)
                    batch = zip(ids, (float(s) for s in scores))
                else:
                    batch = (
                        (i, self._cosine(query, self._deserialize(b)))
                        for i, b in zip(ids, blobs)
                    )

                for id_, score in batch:
                    if len(heap) < limit:
                        heapq.heappush(heap, (score, id_))
                    elif score > heap[0][0]:
                        heapq.heapreplace(heap, (score, id_))

        if skipped:
            logger.warning(
                "VectorBackend.search: skipped %d vector(s) whose stored dimension "
                "differs from %d (stale embedding model?)",
                skipped, self.dimension,
            )
        return [
            {"id": id_, "score": score}
            for score, id_ in sorted(heap, key=lambda t: (-t[0], t[1]))
        ]

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
