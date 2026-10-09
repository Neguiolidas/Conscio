"""Tests for v4.9 knowledge upgrades (the v4.9 audit).

- compact(purge_tombstoned=True): dead sources go regardless of age
- sensitivity column: auto-migration on old stores, label backfill,
  default search never returns secret sources
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conscio.content_store import ContentStore
from conscio.sensitivity import classify_label, validate


def _fresh(tmp: Path) -> ContentStore:
    return ContentStore(db_path=tmp / "cs.db")


def _add(store: ContentStore, label: str, text: str, days_old: int = 0):
    """Index a small source with optional backdated indexed_at."""
    store.index(label, text, "reference", content_type="prose")
    if days_old:
        cutoff_iso = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days_old * 86400))
        store.db.execute("UPDATE sources SET indexed_at=? WHERE label=?",
                         (cutoff_iso, label))
        store.db.commit()
    return label


# --- compact estendido --------------------------------------------------


def test_compact_default_keeps_tombstoned_young(tmp_path: Path) -> None:
    store = _fresh(tmp_path)
    _add(store, "alive", "hello world")
    _add(store, "dead-young", "dead content")
    store.db.execute(
        "INSERT INTO source_tombstones (source_id, reason) VALUES"
        " ((SELECT id FROM sources WHERE label='dead-young'), 'retracted')")
    store.db.commit()
    removed = store.compact(before_days=90)
    assert removed == 0  # age-only sweep: young dead stays (as before)


def test_compact_purge_tombstoned_removes_dead_young(tmp_path: Path) -> None:
    store = _fresh(tmp_path)
    _add(store, "alive", "hello world")
    _add(store, "dead-young", "dead content")
    store.db.execute(
        "INSERT INTO source_tombstones (source_id, reason) VALUES"
        " ((SELECT id FROM sources WHERE label='dead-young'), 'retracted')")
    store.db.commit()
    removed = store.compact(before_days=90, purge_tombstoned=True)
    assert removed == 1
    left = [r["label"] for r in
            store.db.execute("SELECT label FROM sources")]
    assert left == ["alive"]


def test_compact_purge_still_removes_old_alive(tmp_path: Path) -> None:
    store = _fresh(tmp_path)
    _add(store, "old-alive", "old but alive", days_old=200)
    removed = store.compact(before_days=90, purge_tombstoned=True)
    assert removed == 1


# --- sensitivity --------------------------------------------------------


def test_sensitivity_levels_and_validation() -> None:
    assert validate("secret") == "secret"
    assert validate("internal") == "internal"
    with pytest.raises(ValueError):
        validate("top")


def test_classify_label_names_credential_artifacts() -> None:
    assert classify_label("config/.env") == "secret"
    assert classify_label("API_KEY production") == "secret"
    assert classify_label("secrets/vault.json") == "secret"
    assert classify_label("notes from meeting") == "internal"
    assert classify_label("") == "internal"


def test_migration_adds_column_to_old_store(tmp_path: Path) -> None:
    """A pre-v4.9 store gains sensitivity on open, backfill included."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
            source_category TEXT,
            content_hash TEXT
        );
        INSERT INTO sources (label, indexed_at) VALUES ('.env', datetime('now'));
        INSERT INTO sources (label, indexed_at) VALUES ('notes', datetime('now'));
    """)
    conn.commit()
    conn.close()
    store = ContentStore(db_path=db)  # opens -> migrates
    rows = {r["label"]: r["sensitivity"]
            for r in store.db.execute("SELECT label, sensitivity FROM sources")}
    assert rows == {".env": "secret", "notes": "internal"}


def test_default_search_hides_secret_sources(tmp_path: Path) -> None:
    store = _fresh(tmp_path)
    _add(store, "public note", "the launch date is public and the value is clear")
    _add(store, ".env", "SECRET_API_KEY=abc123 value here")
    store.db.execute(
        "UPDATE sources SET sensitivity='secret' WHERE label='.env'")
    store.db.commit()
    hits = store.search("value")
    labels = [store.get_source(h.source_id).label for h in hits]
    assert "public note" in labels
    assert ".env" not in labels


def test_search_include_secrets_opt_in(tmp_path: Path) -> None:
    store = _fresh(tmp_path)
    _add(store, ".env", "SECRET_API_KEY=abc123 value here")
    store.db.execute(
        "UPDATE sources SET sensitivity='secret' WHERE label='.env'")
    store.db.commit()
    assert store.search("value") == []
    hits = store.search("value", include_secrets=True)
    assert len(hits) == 1
    assert store.get_source(hits[0].source_id).label == ".env"
