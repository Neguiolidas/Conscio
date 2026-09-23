"""v4.7 Task 6 — outcome schema: append-only decision outcomes with idempotency.

P1 minimum (Jev 0.97, PRD §4): the data starts to EXIST with provenance.
Schema is immutable; capture hooks are small and non-blocking; a failed
persist is visible, never swallowed; pending is not failure; duplicates
return the original snapshot.
"""
import json
from pathlib import Path

import pytest

from conscio.outcomes import (
    OutcomeRecord,
    OutcomeStore,
    capture_council_outcome,
)


@pytest.fixture
def store(tmp_path: Path) -> OutcomeStore:
    return OutcomeStore(tmp_path / "outcomes.db")


class TestOutcomeSchema:
    def test_record_fields_immutable_shape(self, store):
        rec = OutcomeRecord(
            source="council",
            decision_ref="council-001",
            snapshot={"recommendation": "proceed", "agreement": {"value": 1.0}},
        )
        eid = store.append(rec)
        assert eid > 0
        row = store.get(decision_ref="council-001")
        assert row["source"] == "council"
        assert row["outcome"] == "pending"
        assert row["outcome_ts"] is None
        assert json.loads(row["snapshot"])["recommendation"] == "proceed"

    def test_duplicate_decision_ref_idempotent(self, store):
        first = store.append(OutcomeRecord(
            source="council", decision_ref="dup-1",
            snapshot={"recommendation": "hold"}))
        again = store.append(OutcomeRecord(
            source="council", decision_ref="dup-1",
            snapshot={"recommendation": "CHANGED"}))
        assert first == again, "duplicate returns the original event id"
        row = store.get(decision_ref="dup-1")
        assert json.loads(row["snapshot"])["recommendation"] == "hold", \
            "snapshot is immutable — the duplicate did not rewrite it"

    def test_outcome_update_never_rewrites_snapshot(self, store):
        store.append(OutcomeRecord(
            source="council", decision_ref="upd-1",
            snapshot={"recommendation": "proceed"}))
        store.resolve(decision_ref="upd-1", outcome="success",
                      evidence_ref="test-run-42")
        row = store.get(decision_ref="upd-1")
        assert row["outcome"] == "success"
        assert row["outcome_ts"] is not None
        assert json.loads(row["snapshot"])["recommendation"] == "proceed"

    def test_pending_is_not_failure(self, store):
        store.append(OutcomeRecord(
            source="evaluate", decision_ref="ev-1", snapshot={}))
        row = store.get(decision_ref="ev-1")
        assert row["outcome"] == "pending"
        assert row["outcome_ts"] is None

    def test_unknown_outcome_value_rejected(self, store):
        store.append(OutcomeRecord(
            source="squad", decision_ref="sq-1", snapshot={}))
        with pytest.raises(ValueError):
            store.resolve(decision_ref="sq-1", outcome="exploded")

    def test_resolve_unknown_ref_is_visible_no_op(self, store, caplog):
        ok = store.resolve(decision_ref="ghost", outcome="success")
        assert ok is False
        assert any("ghost" in r.getMessage() for r in caplog.records), \
            "a resolve against a missing decision must be VISIBLE, not silent"

    def test_all_sources_accepted(self, store):
        for src in ("council", "evaluate", "squad", "coherence"):
            eid = store.append(OutcomeRecord(
                source=src, decision_ref=f"{src}-1", snapshot={}))
            assert eid > 0

    def test_unknown_source_rejected(self, store):
        with pytest.raises(ValueError):
            store.append(OutcomeRecord(
                source="tarot", decision_ref="t-1", snapshot={}))


class TestCouncilCaptureHook:
    def test_council_result_captures_with_provenance(self, store):
        result = {
            "question": "ship v4.7?",
            "recommendation": "proceed",
            "agreement": {"value": 1.0, "category": "asserted",
                          "method": "vote_entropy"},
            "votes_summary": {"proceed": 4, "hold": 0, "veto": 0},
        }
        eid = capture_council_outcome(store, result)
        assert eid > 0
        row = store.get_by_event_id(eid)
        snap = json.loads(row["snapshot"])
        assert snap["recommendation"] == "proceed"
        assert snap["agreement"]["value"] == 1.0
        assert row["outcome"] == "pending"