"""Tests for the v4.9 outcomes upgrades (the v4.9 audit, 4, 6).

Item 3: is_test column + auto migration + mark_test() + list filter.
Item 4: 'registrar' accepted as a capture source.
Item 6: 'unknown' accepted as a resolve outcome.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from conscio.outcomes import (
    OUTCOMES,
    SOURCES,
    OutcomeRecord,
    OutcomeStore,
)


def _store(tmp_path: Path) -> OutcomeStore:
    return OutcomeStore(tmp_path / "outcomes.db")


def _capture(store: OutcomeStore, ref: str, source: str = "council") -> int:
    return store.append(OutcomeRecord(
        source=source, decision_ref=ref, snapshot={"q": ref}))


def test_is_test_column_exists_and_defaults_zero(tmp_path: Path) -> None:
    store = _store(tmp_path)
    eid = _capture(store, "council:1:a")
    row = store.get_by_event_id(eid)
    assert row["is_test"] == 0
    store.close()


def test_migration_from_a_schema_without_is_test(tmp_path: Path) -> None:
    """A pre-v4.9 outcomes.db gains the column automatically."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE decision_outcomes (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            decision_ref TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'pending',
            outcome_ts REAL,
            evidence_ref TEXT,
            created_ts REAL NOT NULL
        );
        INSERT INTO decision_outcomes
            (source, decision_ref, snapshot, created_ts)
            VALUES ('council', 'old:1', '{}', 1.0);
    """)
    conn.commit()
    conn.close()
    store = OutcomeStore(db)  # opens -> migrates
    row = store.get("old:1")
    assert row["is_test"] == 0  # migrated, default intact
    store.close()


def test_mark_test_hides_and_unmark_restores(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _capture(store, "keep:me")
    _capture(store, "hide:me")
    assert store.mark_test("hide:me") is True
    visible = [r["decision_ref"] for r in store.list()]
    assert visible == ["keep:me"]
    everything = [r["decision_ref"] for r in store.list(include_tests=True)]
    assert sorted(everything) == ["hide:me", "keep:me"]
    assert store.mark_test("hide:me", unmark=True) is True
    assert sorted(r["decision_ref"] for r in store.list()) == \
        ["hide:me", "keep:me"]
    store.close()


def test_mark_test_unknown_ref_returns_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.mark_test("nope:0") is False
    store.close()


def test_registrar_is_a_valid_source(tmp_path: Path) -> None:
    """the v4.9 audit: the registrar bridge captures as a source."""
    assert "registrar" in SOURCES
    store = _store(tmp_path)
    eid = _capture(store, "registrar:abc123", source="registrar")
    assert eid > 0
    store.close()


def test_registrar_rejected_before_v49(tmp_path: Path) -> None:
    """Dedup guard: only listed sources pass (a typo still fails)."""
    store = _store(tmp_path)
    try:
        _capture(store, "wrong:1", source="registrar ")
    except ValueError:
        pass
    else:
        raise AssertionError("source with a trailing space must be rejected")
    store.close()


def test_unknown_is_a_valid_outcome(tmp_path: Path) -> None:
    """the v4.9 audit: indeterminable is a real verdict."""
    assert "unknown" in OUTCOMES
    store = _store(tmp_path)
    _capture(store, "later:1")
    assert store.resolve("later:1", "unknown") is True
    assert store.get("later:1")["outcome"] == "unknown"
    store.close()


def test_resolve_help_mentions_unknown(tmp_path: Path) -> None:
    """The OUTCOMES set is the CLI's help source; nothing else to test."""
    assert sorted(OUTCOMES) == sorted({
        "pending", "success", "failure", "reverted", "false_positive",
        "unknown"})
