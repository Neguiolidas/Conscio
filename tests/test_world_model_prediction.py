# tests/test_world_model_prediction.py
from datetime import datetime, timedelta

from conscio.coherence import reality_score
from conscio.world_model import WorldModel


def test_record_prediction_returns_surprise(tmp_path):
    wm = WorldModel(tmp_path)
    assert wm.record_prediction("btc", "up", "down") is True
    assert wm.record_prediction("btc", "up", "up") is False


def test_error_rate_window(tmp_path):
    wm = WorldModel(tmp_path)
    wm.record_prediction("a", "x", "y")   # error
    wm.record_prediction("b", "x", "x")   # ok
    assert wm.recent_prediction_error_rate(24) == 0.5


def test_error_rate_empty_is_zero(tmp_path):
    wm = WorldModel(tmp_path)
    assert wm.recent_prediction_error_rate(24) == 0.0


def test_prediction_log_retention_drops_ancient(tmp_path):
    wm = WorldModel(tmp_path)
    old_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    wm._data["prediction_log"] = [
        {"entity": "old", "expected": "a", "actual": "b", "error": 1, "ts": old_ts}
    ]
    wm.record_prediction("new", "a", "a")
    log = wm._data["prediction_log"]
    assert all(e["entity"] != "old" for e in log)   # >7d dropped
    assert any(e["entity"] == "new" for e in log)


def test_prediction_log_hard_cap(tmp_path):
    wm = WorldModel(tmp_path)
    now = datetime.now().isoformat()
    wm._data["prediction_log"] = [
        {"entity": f"e{i}", "expected": "a", "actual": "b", "error": 1, "ts": now}
        for i in range(600)
    ]
    wm.record_prediction("last", "a", "a")
    assert len(wm._data["prediction_log"]) == 500
    assert wm._data["prediction_log"][-1]["entity"] == "last"


# --- validate_prediction ---


def test_validate_prediction_marks_correct(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc rises", "bullish", 0.8)  # index 0
    wm.validate_prediction(0, True)  # mark as correct
    assert wm._data["predictions"][0]["validated"] is True
    assert "validated_at" in wm._data["predictions"][0]


def test_validate_prediction_marks_incorrect(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc rises", "bullish", 0.8)  # index 0
    wm.validate_prediction(0, False)
    assert wm._data["predictions"][0]["validated"] is False


def test_validate_prediction_out_of_bounds_noop(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc rises", "bullish", 0.8)
    # index 5 doesn't exist — should not raise
    wm.validate_prediction(5, True)
    assert len(wm._data["predictions"]) == 1
    assert wm._data["predictions"][0].get("validated") is None


def test_validate_prediction_persists(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc rises", "bullish", 0.8)
    wm.validate_prediction(0, True)
    wm2 = WorldModel(tmp_path)
    assert wm2._data["predictions"][0]["validated"] is True


# --- subgraph ---


def test_subgraph_returns_entity_and_relations(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    wm.add_entity("eth", "asset", state="neutral")
    wm.add_relation("btc", "correlates_with", "eth")
    result = wm.subgraph("btc", depth=1)
    assert "btc(asset): bullish" in result
    assert "correlates_with" in result
    assert "eth" in result


def test_subgraph_depth_limits_traversal(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("a", "type")
    wm.add_entity("b", "type")
    wm.add_entity("c", "type")
    wm.add_relation("a", "links", "b")
    wm.add_relation("b", "links", "c")
    # depth=1 from a: visits a, then follows relation to b (depth 0)
    # From b at depth 0, traversal stops. But b's relations include a and c.
    # Since traversal is bidirectional, we get b's relations too.
    result = wm.subgraph("a", depth=1)
    assert "b" in result
    # Note: bidirectional traversal means depth=1 reaches b and its connections
    # depth=2 goes further
    result2 = wm.subgraph("a", depth=2)
    assert "c" in result2


def test_subgraph_missing_entity(tmp_path):
    wm = WorldModel(tmp_path)
    result = wm.subgraph("nonexistent")
    assert result == "Entity not found."


# --- prediction wiring: add_entity re-perception feeds the reality signal ---
# Regression: record_prediction had ZERO production callers, so prediction_log
# was always empty -> recent_prediction_error_rate() -> reality_score() pinned
# at 1.0 forever. The world's SOLE prod write path is add_entity (via
# content_layer.perceive), so that is where "prior belief vs new observation"
# must be recorded.


def test_reperception_records_prediction_outcome(tmp_path):
    """Re-perceiving a KNOWN entity logs a surprise (state changed, error=1) and
    a confirmation (state unchanged, error=0) — the production producer for
    recent_prediction_error_rate (the coherence 'reality' dimension)."""
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")   # first obs: no prior belief
    assert wm._data["prediction_log"] == []

    wm.add_entity("btc", "asset", state="bearish")   # surprise: bullish -> bearish
    wm.add_entity("btc", "asset", state="bearish")   # confirmation: bearish -> bearish
    log = wm._data["prediction_log"]
    assert len(log) == 2
    assert log[0]["expected"] == "bullish"
    assert log[0]["actual"] == "bearish"
    assert log[0]["error"] == 1
    assert log[1]["error"] == 0
    assert wm.recent_prediction_error_rate(24) == 0.5


def test_first_observation_and_blank_state_do_not_log(tmp_path):
    """No prior belief (first add) or a blank new state must not pollute the log."""
    wm = WorldModel(tmp_path)
    wm.add_entity("eth", "asset", state="neutral")   # first: no prior -> skip
    wm.add_entity("eth", "asset")                    # blank new state -> skip
    assert wm._data["prediction_log"] == []
    assert wm.recent_prediction_error_rate(24) == 0.0


def test_reperception_outcome_persists(tmp_path):
    """The recorded outcome survives a reload (single save inside add_entity)."""
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    wm.add_entity("btc", "asset", state="bearish")
    wm2 = WorldModel(tmp_path)
    assert len(wm2._data["prediction_log"]) == 1
    assert wm2._data["prediction_log"][0]["error"] == 1


def test_reality_score_reflects_reperception_drift(tmp_path):
    """End-to-end: the coherence 'reality' dimension is no longer pinned at 1.0
    once perception surprises the model."""
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    assert reality_score(wm) == 1.0                  # no drift observed yet
    wm.add_entity("btc", "asset", state="bearish")   # one surprise in the window
    assert reality_score(wm) < 1.0
