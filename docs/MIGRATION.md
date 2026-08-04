# Conscio — Migration Guide

Migrate your data from MemPalace/ChromaDB to Conscio's native backends.

---

## Table of Contents

1. [What Changed](#what-changed)
2. [Prerequisites](#prerequisites)
3. [Step 1: Ingest MemPalace content into Conscio](#step-1-ingest-mempalace-content-into-conscio)
4. [Step 2: Migrate vector backend (numpy → sqlite-vec → HNSW)](#step-2-migrate-vector-backend)
5. [Step 3: Verify](#step-3-verify)
6. [Step 4: Uninstall old packages](#step-4-uninstall-old-packages)
7. [Troubleshooting](#troubleshooting)

---

## What Changed

Conscio v3.9.7 replaces ChromaDB with three native vector backends:

| Backend | Search speed | Setup | When to use |
|---------|-------------|-------|-------------|
| **HNSW** (hnswlib) | 0.6–3ms | `pip install hnswlib` | Production, >1k vectors |
| **sqlite-vec** | 17ms | `pip install sqlite-vec` | Mid-tier, no extra RAM |
| **numpy** (default) | 180ms | zero deps | Fresh installs, <1k vectors |

Auto-detect priority: HNSW > sqlite-vec > numpy.
No env vars needed — if `hnsw.db` exists, HNSW is used automatically.

---

## Prerequisites

```bash
# Python 3.12+ required (3.14 does NOT compile hnswlib)
python3.12 --version

# Install Conscio
pip install conscio

# Install vector backends (at least one)
pip install sqlite-vec        # 10x faster than numpy
pip install hnswlib            # 50x faster than numpy (recommended)
```

---

## Step 1: Ingest MemPalace content into Conscio

MemPalace stored documents in ChromaDB (`chroma.sqlite3`). Conscio uses its own
ContentStore (FTS5 + vector). To migrate your content:

### Option A: If you still have the MemPalace chroma.sqlite3

```python
from conscio import ConsciousnessEngine
from pathlib import Path
import sqlite3

# Start Conscio engine
eng = ConsciousnessEngine(
    model_name='local',
    storage_path='~/.conscio/runtime',
)

# Open old ChromaDB
chroma_path = Path('~/mempalace/palace/chroma.sqlite3')
conn = sqlite3.connect(str(chroma_path))
conn.row_factory = sqlite3.Row

# Read documents from embedding_metadata
rows = conn.execute("""
    SELECT DISTINCT em.id, em.string_value as content
    FROM embedding_metadata em
    WHERE em.key = 'chroma:document' AND em.string_value IS NOT NULL
""").fetchall()

# Ingest each document into Conscio ContentStore
count = 0
for row in rows:
    content = row['content']
    if content and len(content) > 10:
        eng.content_store.index(
            source=f'mempalace:{row["id"]}',
            content=content,
            category='external',
            content_type='prose',
        )
        count += 1

conn.close()
eng.close()
print(f'Migrated {count} documents from MemPalace to Conscio')
```

### Option B: If you already have content in Conscio (v3.2+)

If you ran Conscio alongside MemPalace, your content is already in
`content_store.db`. No migration needed — just update the vector backend
(Step 2).

### Option C: Ingest from raw files

```python
from conscio import ConsciousnessEngine

eng = ConsciousnessEngine(
    model_name='local',
    storage_path='~/.conscio/runtime',
)

# Ingest an entire directory of markdown/text files
stats = eng.ingest_directory(
    path='~/mempalace/diary',
    category='reference',
    chunk_size=2000,
    overlap=0.2,
)
print(f'Ingested {stats["chunks"]} chunks from {stats["files"]} files')
eng.close()
```

---

## Step 2: Migrate vector backend

### From numpy to sqlite-vec (10x faster)

```bash
# One command — detects format, migrates, verifies, backs up
conscio migrate-vectors --storage ~/.conscio/runtime
```

This will:
1. Detect if your `vectors.db` uses numpy BLOB format
2. Create a backup (`.bak`)
3. Migrate all vectors to sqlite-vec `vec0` virtual table
4. Verify search rankings match the original
5. Atomic swap (old → `.numpy.bak`, new → `vectors.db`)

### From sqlite-vec to HNSW (50x faster)

```bash
# Ensure hnswlib is installed
pip install hnswlib

# Generate HNSW index from existing vectors
python3 -c "
from pathlib import Path
from conscio.vector_backend import SqliteVecBackend, HNSWBackend
import array, time

storage = Path.home() / '.conscio/runtime'

# Read from sqlite-vec
src = SqliteVecBackend(db_path=storage / 'vectors.db', dimension=384)
print(f'Source: {src.stats()}')

# Create HNSW at separate path (don't clobber vectors.db)
dst = HNSWBackend(
    db_path=storage / 'hnsw.db',
    dimension=384,
    # Tuned for real-world embeddings:
    M=32,              # graph connectivity (default 32, was 16)
    ef_construction=400,  # build quality (default 400, was 200)
)
print(f'HNSW params: M={dst.M} ef_construction={dst.ef_construction}')

# Read all vectors
conn = src._conn_get()
cur = conn.execute('SELECT id, embedding FROM vec_chunks')
all_vecs = []
for row in cur:
    arr = array.array('f')
    arr.frombytes(row['embedding'])
    all_vecs.append((row['id'], arr.tolist()))

print(f'Read {len(all_vecs)} vectors')

# One-shot batch insert (O(n log n) graph build)
t0 = time.time()
dst.add_batch(all_vecs)
print(f'Ingest: {time.time()-t0:.1f}s')

dst.close()
src.close()
print(f'HNSW index: {storage}/hnsw.db')
print('Done! Engine will auto-detect HNSW on next startup.')
"
```

### Auto-detect (no config needed)

Conscio auto-detects the best available backend on startup:

```
1. HNSW (if hnsw.db exists + hnswlib installed)  →  3ms search
2. sqlite-vec (if vectors.db has vec_chunks)     → 17ms search
3. numpy (default, zero deps)                    → 180ms search
```

To force a specific backend, set environment variable:
```bash
export CONSCIO_VEC_BACKEND=hnsw       # or sqlite_vec, or numpy
```

---

## Step 3: Verify

```python
from conscio import ConsciousnessEngine
from pathlib import Path

eng = ConsciousnessEngine(
    model_name='local',
    storage_path='~/.conscio/runtime',
)

# Check vector backend
print(f'Backend: {type(eng.vector_backend).__name__}')
print(f'Stats: {eng.vector_backend.stats()}')

# Test search
import random, time
rng = random.Random(42)
q = [rng.gauss(0, 1) for _ in range(384)]
t0 = time.time()
results = eng.vector_backend.search(q, limit=5)
print(f'Search: {(time.time()-t0)*1000:.1f}ms, {len(results)} results')

# Test FTS5 recall
results = eng.recall('memory model', k=3)
print(f'Recall: {len(results)} snippets')

eng.close()
```

Expected output:
```
Backend: HNSWBackend
Stats: {'vectors': 37042, 'dimension': 384}
Search: 2.8ms, 5 results
Recall: 3 snippets
```

---

## Step 4: Uninstall old packages

```bash
# Uninstall MemPalace
pipx uninstall mempalace

# Uninstall ChromaDB
pip uninstall chromadb chromadb-client

# Remove data directories
rm -rf ~/.mempalace ~/mempalace ~/.mempalace-backup-* ~/.cache/chroma

# Remove binaries
rm -f ~/.local/bin/mempalace ~/.local/bin/mempalace-mcp

# Remove old configs
rm -f ~/clawd/mempalace.yaml ~/nextep-session-logs/mempalace.yaml

# Remove Hermes skill (if present)
rm -rf ~/.hermes/skills/mempalace-preservation

# Verify cleanup
find / -maxdepth 5 -iname "*chroma*" -not -path "/proc/*" -not -path "/sys/*" 2>/dev/null
find / -maxdepth 5 -iname "*mempalace*" -not -path "/proc/*" -not -path "/sys/*" 2>/dev/null
# Both should return empty (or only system libs like libchromaprint)
```

---

## Troubleshooting

### "hnswlib won't compile on Python 3.14"

Use Python 3.12 instead. hnswlib requires C++ compilation that fails on 3.14.
sqlite-vec (pure wheel) works on any Python.

### "Recall returns 0 snippets"

Check that `content_store.db` exists and has content:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('$HOME/.conscio/runtime/content_store.db')
print('chunks:', conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])
print('sources:', conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0])
conn.close()
"
```

If chunks = 0, you need to ingest content first (Step 1).

### "HNSW recall is low with random vectors"

HNSW is an approximate nearest neighbor algorithm. With **random** vectors (no
semantic structure), recall will be low (~60%). With **real embedding** vectors
(which have semantic structure), recall is 99%+.

Always test recall with real vectors from your database, not random ones.

### "sentence_transformers import fails"

NumPy version conflict. Pin numpy < 2:
```bash
pip install 'numpy<2' --force
```

### "MCP server still uses old backend"

After changing backend, reload the MCP server:
- In Hermes: run `reload-mcp` command
- Or restart the gateway: `hermes gateway restart` (from a separate shell)

The MCP process caches code in memory — it needs a restart to pick up changes.

---

## File Layout (after migration)

```
~/.conscio/runtime/
├── hnsw.db              # HNSW index (O(log n) search, ~3ms)
├── hnsw.meta.db         # HNSW ID mapping (SQLite, vector IDs ↔ string IDs)
├── vectors.db           # sqlite-vec fallback (vec0 table, ~17ms)
├── vectors.db.numpy.bak # numpy backup (original format, ~180ms)
├── content_store.db     # ContentStore (FTS5 + chunks, 49k chunks)
├── conscio.db           # EventBus + TokenTracker (system events)
└── mcp_seen.db          # MCP dedup tracking
```

---

*Generated: 2026-08-04 · Conscio v3.9.7*
