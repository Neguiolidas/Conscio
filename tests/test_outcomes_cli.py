"""v4.7 audit fix — the outcome store needs a first-class consumer.

The auditor found the orphan: capture_council_outcome writes pending rows on
every council, but NOTHING in production calls OutcomeStore.resolve() — the
loop can never close outside tests, and calibration stays unmeasurable.

The CLI is the first-class path (same pattern as `conscio honesty recent`):
`conscio outcomes list` shows pending decisions; `conscio outcomes resolve`
closes one with evidence. TDD: this file goes RED first.
"""
from pathlib import Path

import pytest

from conscio.cli import _cmd_outcomes
from conscio.outcomes import OutcomeRecord, OutcomeStore


@pytest.fixture
def store(tmp_path: Path) -> OutcomeStore:
    return OutcomeStore(tmp_path / "outcomes.db")


def _run(capsys, *argv) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("outcomes")
    p_sub = p.add_subparsers(dest="outcomes_command")
    p_list = p_sub.add_parser("list")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--storage", default="")
    p_resolve = p_sub.add_parser("resolve")
    p_resolve.add_argument("--storage", default="")
    p_resolve.add_argument("decision_ref")
    p_resolve.add_argument("outcome")
    p_resolve.add_argument("--evidence", default="")
    args = parser.parse_args(["outcomes", *argv])
    return _cmd_outcomes(args)


class TestOutcomesCLI:
    def test_list_shows_pending(self, store, capsys):
        store.append(OutcomeRecord(
            source="council", decision_ref="c-1",
            snapshot={"recommendation": "proceed"}))
        rc = _run(capsys, "list", "--storage", str(store_path(store)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "c-1" in out
        assert "pending" in out

    def test_list_empty_is_visible_not_silent(self, store, capsys):
        rc = _run(capsys, "list", "--storage", str(store_path(store)))
        assert rc == 0
        assert "no decisions recorded yet" in capsys.readouterr().out

    def test_resolve_closes_the_loop(self, store, capsys):
        store.append(OutcomeRecord(
            source="council", decision_ref="c-2", snapshot={}))
        rc = _run(capsys, "resolve", "--storage", str(store_path(store)),
                  "c-2", "success", "--evidence", "run-42")
        assert rc == 0
        row = store.get("c-2")
        assert row["outcome"] == "success"
        assert row["evidence_ref"] == "run-42"
        assert "resolved" in capsys.readouterr().out

    def test_resolve_ghost_is_visible(self, store, capsys):
        rc = _run(capsys, "resolve", "--storage", str(store_path(store)),
                  "ghost-1", "success")
        assert rc == 1
        out = capsys.readouterr().out
        assert "ghost-1" in out and "not found" in out

    def test_resolve_unknown_outcome_rejected(self, store, capsys):
        store.append(OutcomeRecord(
            source="council", decision_ref="c-3", snapshot={}))
        rc = _run(capsys, "resolve", "--storage", str(store_path(store)),
                  "c-3", "exploded")
        assert rc == 1
        assert "invalid outcome" in capsys.readouterr().out.lower()


def store_path(store: OutcomeStore) -> str:
    """Recover the db dir from an open store (its conn string)."""
    db = store._conn.execute("PRAGMA database_list").fetchall()
    return str(Path(db[0][2]).parent)
