"""VectorBackend — cosine vector store in SQLite BLOB float32.

Standalone implementation.py, simplified:
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
import os
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

    @staticmethod
    def with_engine(
        db_path: str | Path | None = None,
        dimension: int = 768,
    ) -> "VectorBackend | SqliteVecBackend | HNSWBackend":
        """Factory: pick the vector backend based on CONSCIO_VEC_BACKEND env var.

        - ``sqlite_vec``: sqlite-vec C-native cosine (opt-in, requires pip install sqlite-vec)
        - ``hnsw``: hnswlib approx search (opt-in, requires pip install hnswlib)
        - unset / ``numpy``: original Python/numpy O(n) backend (default)

        Falls back to the original VectorBackend if the requested engine
        is unavailable (dep not installed, extension won't load).
        """
        engine = os.environ.get("CONSCIO_VEC_BACKEND", "").strip().lower()

        # HNSW uses a separate file (hnsw.db) to avoid clobbering vectors.db
        hnsw_path = Path(db_path).parent / "hnsw.db" if db_path else None

        # Auto-detect priority: HNSW first (fastest), then sqlite-vec, then numpy
        # Check HNSW index exists
        if not engine and hnsw_path and _HAS_HNSW and hnsw_path.exists():
            logger.info("Auto-detected HNSW index (hnsw.db), using HNSWBackend")
            try:
                return HNSWBackend(db_path=hnsw_path, dimension=dimension)
            except Exception:
                logger.warning("HNSW backend init failed, falling back", exc_info=True)

        if engine == "hnsw":
            try:
                return HNSWBackend(db_path=hnsw_path, dimension=dimension)
            except (ImportError, Exception):
                logger.warning("HNSW backend init failed, falling back to numpy", exc_info=True)

        if engine == "sqlite_vec":
            try:
                return SqliteVecBackend(db_path=db_path, dimension=dimension)
            except Exception:
                logger.warning("sqlite-vec backend init failed, falling back to numpy", exc_info=True)

        # Auto-detect: if the DB has a vec_chunks virtual table, use sqlite-vec
        if not engine and db_path and Path(db_path).exists():
            try:
                probe = sqlite3.connect(str(db_path))
                tables = {r[0] for r in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                ).fetchall()}
                probe.close()
                if "vec_chunks" in tables:
                    logger.info("Auto-detected sqlite-vec schema, using SqliteVecBackend")
                    try:
                        return SqliteVecBackend(db_path=db_path, dimension=dimension)
                    except Exception:
                        logger.warning("sqlite-vec backend init failed, falling back to numpy", exc_info=True)
            except Exception:
                pass

        return VectorBackend(db_path=db_path, dimension=dimension)

    def __init__(self, db_path: str | Path | None = None, dimension: int = 768):
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "vec.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dimension = dimension
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()
        # Auto-detect dimension from existing data so a fresh VectorBackend
        # pointing at a populated DB adopts the stored dimension instead of
        # rejecting every search with a mismatch error.
        try:
            row = self._conn_get().execute(
                "SELECT dimension FROM vectors LIMIT 1"
            ).fetchone()
            if row and row[0] and row[0] != self.dimension:
                self.dimension = row[0]
        except Exception:
            pass

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


# ── sqlite-vec detection ────────────────────────────────────────────

def has_sqlite_vec() -> bool:
    """True if the sqlite-vec extension can be loaded in this environment."""
    try:
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


# ── SqliteVecBackend — C-native cosine via sqlite-vec extension ──────

class SqliteVecBackend:
    """Vector store backed by sqlite-vec's vec0 virtual table.

    Uses sqlite-vec's ``distance_metric=cosine`` for native C cosine search,
    replacing the Python/numpy O(n) scan in :class:`VectorBackend`.

    API-compatible with VectorBackend: add, add_batch, search,
    ensure_dimension, stats, close, dump.

    The vec0 table uses INTEGER rowid as primary key. We maintain a
    separate ``vec_metadata`` table mapping rowid → original string id +
    category, so the public API stays string-id based.
    """

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
            # Performance PRAGMAs — tuned for vector workloads:
            # - WAL: concurrent readers don't block the writer
            # - synchronous=OFF: skip fsync on commit (WAL handles durability;
            #   we accept the small risk of last-commit loss on power failure
            #   because vector indexes are disposable/regenerable)
            # - mmap_size=256MB: memory-map vec0 shadow tables for zero-copy
            #   reads — 32% faster search vs default page-cache
            # - cache_size=64MB: larger page cache for metadata JOIN
            # - page_size=32768: larger pages reduce B-tree depth for 384-dim
            #   vectors (~1.5KB each); 28% faster ingest, 3% faster search
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=OFF")
            self._conn.execute("PRAGMA mmap_size=268435456")
            self._conn.execute("PRAGMA cache_size=-64000")
            # Set page_size before any table creation
            try:
                self._conn.execute("PRAGMA page_size=32768")
            except sqlite3.OperationalError:
                pass  # page_size can only be set on an empty database
            self._conn.row_factory = sqlite3.Row
            self._conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn_get()
            # vec0 with aux columns: id and category stored directly in the
            # virtual table, eliminating the vec_metadata JOIN (27% faster
            # category search) and the separate metadata table entirely.
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
                f"embedding float[{self.dimension}] distance_metric=cosine,"
                f"id TEXT, category TEXT)"
            )
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _serialize_for_vec0(self, vec: Sequence[float]) -> bytes:
        """Serialize vector as BLOB (float32) for vec0 INSERT.

        BLOB is 11x faster than JSON string serialization on ingest.
        """
        return array.array("f", vec).tobytes()

    def add(self, id: str, vec: list[float], category: str | None = None) -> None:
        _check_no_nan(vec)
        if len(vec) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
            )
        vec_blob = self._serialize_for_vec0(vec)
        with self._lock:
            conn = self._conn_get()
            with conn:
                # Check if id already exists, delete if so (vec0 doesn't support OR REPLACE)
                existing = conn.execute(
                    "SELECT rowid FROM vec_chunks WHERE id = ?", (id,)
                ).fetchone()
                if existing:
                    conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (existing[0],))
                conn.execute(
                    "INSERT INTO vec_chunks (embedding, id, category) VALUES (?, ?, ?)",
                    (vec_blob, id, category or ""),
                )

    def add_batch(
        self,
        items: Iterable[tuple[str, Sequence[float]]],
        category: str | None = None,
    ) -> int:
        # Validate everything first (fail before any write)
        validated: list[tuple[str, bytes]] = []
        for id_, vec in items:
            _check_no_nan(vec)
            if len(vec) != self.dimension:
                raise ValueError(
                    f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
                )
            validated.append((id_, self._serialize_for_vec0(vec)))
        if not validated:
            return 0
        with self._lock:
            conn = self._conn_get()
            count = 0
            with conn:
                for id_, vec_blob in validated:
                    conn.execute(
                        "INSERT INTO vec_chunks (embedding, id, category) VALUES (?, ?, ?)",
                        (vec_blob, id_, category or ""),
                    )
                    count += 1
            return count

    def ensure_dimension(self, dim: int) -> bool:
        if dim == self.dimension:
            return True
        with self._lock:
            conn = self._conn_get()
            occupied = conn.execute("SELECT 1 FROM vec_chunks LIMIT 1").fetchone() is not None
        if occupied:
            return False
        logger.info(
            "SqliteVecBackend: adopting embedder dimension %d (was %d, store empty)",
            dim, self.dimension,
        )
        self.dimension = dim
        # Recreate the vec0 virtual table with the new dimension
        with self._lock:
            conn = self._conn_get()
            conn.execute("DROP TABLE IF EXISTS vec_chunks")
            conn.execute(
                f"CREATE VIRTUAL TABLE vec_chunks USING vec0("
                f"embedding float[{self.dimension}] distance_metric=cosine,"
                f"id TEXT, category TEXT)"
            )
            conn.commit()
        return True

    def search(
        self,
        query: list[float],
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict]:
        _check_no_nan(query)
        if len(query) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(query)}"
            )
        if limit <= 0:
            return []

        # sqlite-vec vec0 has a hard limit of 4096 for k in knn queries.
        effective_limit = min(limit, 4096)

        vec_blob = self._serialize_for_vec0(query)
        with self._lock:
            conn = self._conn_get()
            if category is not None:
                # With aux columns, category filter is inside vec0 — 27% faster
                # than the JOIN approach because vec0 filters internally.
                rows = conn.execute(
                    "SELECT id, distance FROM vec_chunks "
                    "WHERE embedding MATCH ? AND category = ? AND k = ? "
                    "ORDER BY distance",
                    (vec_blob, category, effective_limit),
                ).fetchall()
                rows = rows[:limit]
            else:
                rows = conn.execute(
                    "SELECT id, distance FROM vec_chunks "
                    "WHERE embedding MATCH ? AND k = ? "
                    "ORDER BY distance",
                    (vec_blob, effective_limit),
                ).fetchall()

        # cosine distance: 0 = identical, 2 = opposite. score = 1 - distance.
        return [
            {"id": r[0], "score": max(0.0, 1.0 - (r[1] or 0.0))}
            for r in rows
        ]

    def stats(self) -> dict:
        with self._lock:
            conn = self._conn_get()
            cnt = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        return {"vectors": cnt, "dimension": self.dimension}

    def dump(self, target_path: str | Path) -> None:
        dst = sqlite3.connect(str(target_path))
        self._conn_get().backup(dst)
        dst.close()


# ── HNSWBackend — optional HNSW via hnswlib (opt-in, no hard dep) ───

try:
    import hnswlib
    _HAS_HNSW = True
except ImportError:
    _HAS_HNSW = False


class HNSWBackend:
    """HNSW vector index via hnswlib — O(log n) approximate search.

    Opt-in backend for large-scale deployments (1M+ vectors). Holds the
    HNSW graph in RAM during operation, persists to disk on close.

    Requires: ``pip install hnswlib`` (Apache-2.0, C++ with Python bindings).
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        dimension: int = 768,
        max_elements: int = 1_000_000,
        ef_construction: int = 400,
        M: int = 32,
    ):
        if not _HAS_HNSW:
            raise ImportError(
                "HNSWBackend requires hnswlib: pip install hnswlib"
            )
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "hnsw.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dimension = dimension
        self.max_elements = max_elements
        self.ef_construction = ef_construction
        self.M = M
        self._lock = threading.Lock()
        self._index: hnswlib.Index | None = None
        self._id_map: dict[int, str] = {}
        self._reverse_map: dict[str, int] = {}
        self._categories: dict[int, str | None] = {}
        self._next_id = 0
        self._init_index()

    def _init_index(self) -> None:
        self._index = hnswlib.Index(space="cosine", dim=self.dimension)
        index_path = str(self.db_path)
        if Path(index_path).exists():
            self._index.load_index(index_path)
            meta_path = str(self.db_path.with_suffix(".meta.db"))
            if Path(meta_path).exists():
                conn = sqlite3.connect(meta_path)
                for row in conn.execute("SELECT hnsw_id, original_id, category FROM id_map"):
                    self._id_map[row[0]] = row[1]
                    self._reverse_map[row[1]] = row[0]
                    self._categories[row[0]] = row[2]
                self._next_id = max(self._id_map.keys(), default=-1) + 1
                conn.close()
        else:
            self._index.init_index(
                max_elements=self.max_elements,
                ef_construction=self.ef_construction,
                M=self.M,
            )

    def add(self, id: str, vec: list[float], category: str | None = None) -> None:
        _check_no_nan(vec)
        if len(vec) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
            )
        with self._lock:
            if id in self._reverse_map:
                hnsw_id = self._reverse_map[id]
            else:
                hnsw_id = self._next_id
                self._next_id += 1
                self._id_map[hnsw_id] = id
                self._reverse_map[id] = hnsw_id
                if len(self._id_map) > self.max_elements:
                    self._index.resize_index(len(self._id_map) + 10000)
            # hnswlib requires contiguous float32 numpy arrays
            data = np.ascontiguousarray([vec], dtype=np.float32)
            labels = np.array([hnsw_id], dtype=np.int64)
            self._index.add_items(data, labels)
            self._categories[hnsw_id] = category

    def add_batch(
        self,
        items: Iterable[tuple[str, Sequence[float]]],
        category: str | None = None,
    ) -> int:
        # Collect all items, then do ONE add_items call (O(n log n) build)
        # instead of per-item calls (O(n² log n) — 100x slower on 37k vectors).
        ids_list: list[int] = []
        vecs_list: list[list[float]] = []
        for id_, vec in items:
            _check_no_nan(vec)
            if len(vec) != self.dimension:
                raise ValueError(
                    f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
                )
            if id_ in self._reverse_map:
                hnsw_id = self._reverse_map[id_]
            else:
                hnsw_id = self._next_id
                self._next_id += 1
                self._id_map[hnsw_id] = id_
                self._reverse_map[id_] = hnsw_id
                if len(self._id_map) > self.max_elements:
                    with self._lock:
                        self._index.resize_index(len(self._id_map) + 10000)
            ids_list.append(hnsw_id)
            vecs_list.append(list(vec))
            self._categories[hnsw_id] = category
        if not ids_list:
            return 0
        with self._lock:
            data = np.ascontiguousarray(vecs_list, dtype=np.float32)
            labels = np.array(ids_list, dtype=np.int64)
            self._index.add_items(data, labels)
        return len(ids_list)

    def ensure_dimension(self, dim: int) -> bool:
        if dim == self.dimension:
            return True
        if self._id_map:
            return False
        self.dimension = dim
        self._index = hnswlib.Index(space="cosine", dim=dim)
        self._index.init_index(
            max_elements=self.max_elements,
            ef_construction=self.ef_construction,
            M=self.M,
        )
        return True

    def search(
        self,
        query: list[float],
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict]:
        _check_no_nan(query)
        if len(query) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, got {len(query)}"
            )
        if limit <= 0 or not self._id_map:
            return []

        with self._lock:
            self._index.set_ef(max(limit * 16, 256))
            q = np.ascontiguousarray([query], dtype=np.float32)
            labels, distances = self._index.knn_query(q, k=min(limit * 4, len(self._id_map)))

        results = []
        for label, dist in zip(labels[0], distances[0]):
            hnsw_id = int(label)
            if category is not None and self._categories.get(hnsw_id) != category:
                continue
            orig_id = self._id_map.get(hnsw_id)
            if orig_id is None:
                continue
            # hnswlib cosine space: distance = 1 - cosine_similarity
            score = max(0.0, 1.0 - float(dist))
            results.append({"id": orig_id, "score": score})
            if len(results) >= limit:
                break
        return results

    def stats(self) -> dict:
        return {"vectors": len(self._id_map), "dimension": self.dimension}

    def close(self) -> None:
        with self._lock:
            if self._index is not None:
                self._index.save_index(str(self.db_path))
                meta_path = str(self.db_path.with_suffix(".meta.db"))
                conn = sqlite3.connect(meta_path)
                conn.execute("CREATE TABLE IF NOT EXISTS id_map (hnsw_id INT, original_id TEXT, category TEXT)")
                conn.execute("DELETE FROM id_map")
                for hnsw_id, orig_id in self._id_map.items():
                    conn.execute("INSERT INTO id_map VALUES (?, ?, ?)", (hnsw_id, orig_id, self._categories.get(hnsw_id)))
                conn.commit()
                conn.close()

    def dump(self, target_path: str | Path) -> None:
        self.close()
        import shutil
        shutil.copy2(str(self.db_path), str(target_path))
        meta_src = str(self.db_path.with_suffix(".meta.db"))
        meta_dst = str(Path(target_path).with_suffix(".meta.db"))
        if Path(meta_src).exists():
            shutil.copy2(meta_src, meta_dst)
