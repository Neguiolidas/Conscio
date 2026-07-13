"""Wiring tests: the if-then prediction store gets a prod producer + consumer.

`_data['predictions']` (add_prediction / validate_prediction)
was a dead store: no production code ever wrote to it or read from it. The
wiring contract:

  PRODUCE  — engine.reflect() PREDICT stage (the docstring's step 4) generates
             deterministic persistence predictions for recently-changed
             entities: "if <entity> is observed again, then state is <state>".
  VALIDATE — content_layer.perceive() checks pending predictions against the
             freshly perceived entities and marks them correct/incorrect.
             It does NOT double-log into the prediction error-rate log —
             add_entity() re-perception remains the sole reality producer.
  SURFACE  — WorldModel.query() includes matching predictions, honouring its
             own docstring ("entities, relations, and predictions").
"""
import pytest

from conscio.engine import ConsciousnessEngine
from conscio.world_model import PREDICTIONS_MAX, WorldModel



def _preds(wm, keyword=""):
    """Read predictions straight from persisted state (replaces removed accessor)."""
    preds = wm._data["predictions"]
    if not keyword:
        return preds
    kw = keyword.lower()
    return [p for p in preds if kw in p["if"].lower() or kw in p["then"].lower()]

@pytest.fixture
def engine(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    yield eng
    eng.close()


# --- PRODUCE: generate_persistence_predictions ---

def test_generate_creates_pending_prediction(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    added = wm.generate_persistence_predictions()
    assert added == 1
    preds = _preds(wm, "btc")
    assert len(preds) == 1
    p = preds[0]
    assert p["entity"] == "btc"
    assert p["then"] == "bullish"
    assert "validated" not in p
    assert 0.1 <= p["confidence"] <= 0.9


def test_generate_dedups_pending(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    wm.generate_persistence_predictions()
    assert wm.generate_persistence_predictions() == 0
    assert len(_preds(wm, "btc")) == 1


def test_generate_allows_fresh_prediction_after_validation(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    wm.generate_persistence_predictions()
    wm.validate_prediction(0, True)
    assert wm.generate_persistence_predictions() == 1


def test_generate_skips_stateless_entities(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("mystery", "thing")  # no state
    assert wm.generate_persistence_predictions() == 0
    assert _preds(wm) == []


def test_generate_confidence_tracks_error_rate(tmp_path):
    wm = WorldModel(tmp_path)
    wm.record_prediction("a", "x", "y")   # error
    wm.record_prediction("b", "x", "x")   # ok      -> rate 0.5
    wm.add_entity("btc", "asset", state="bullish")
    wm.generate_persistence_predictions()
    assert _preds(wm, "btc")[0]["confidence"] == pytest.approx(0.5)


# --- VALIDATE: validate_predictions_against ---

def test_validate_against_marks_correct(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    n = wm.validate_predictions_against({"btc": {"type": "asset", "state": "bullish"}})
    assert n == 1
    assert _preds(wm, "btc")[0]["validated"] is True


def test_validate_against_marks_incorrect(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    wm.validate_predictions_against({"btc": {"type": "asset", "state": "bearish"}})
    assert _preds(wm, "btc")[0]["validated"] is False


def test_validate_against_skips_validated_unknown_and_legacy(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    wm.add_prediction("eth is observed again", "quiet", 0.8, entity="eth")
    wm.add_prediction("legacy no-entity", "whatever", 0.5)  # pre-wiring shape
    wm.validate_prediction(0, True)
    stamp = _preds(wm)[0]["validated_at"]
    n = wm.validate_predictions_against({"btc": {"state": "bearish"},
                                         "legacy": {"state": "whatever"}})
    assert n == 0  # already-validated, absent entity, and legacy all skipped
    preds = _preds(wm)
    assert preds[0]["validated"] is True                 # not re-flipped
    assert preds[0]["validated_at"] == stamp
    assert "validated" not in preds[1]                   # eth not perceived
    assert "validated" not in preds[2]                   # legacy untouched


def test_validate_against_blank_state_is_no_evidence(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    assert wm.validate_predictions_against({"btc": {"state": ""}}) == 0
    assert "validated" not in _preds(wm)[0]


def test_validate_against_does_not_touch_error_log(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    wm.validate_predictions_against({"btc": {"state": "bearish"}})
    # add_entity re-perception is the sole reality producer; explicit if-then
    # validation must not double-count into the error rate.
    assert wm.recent_prediction_error_rate(24) == 0.0


# --- SURFACE: query() includes predictions ---

def test_query_surfaces_matching_prediction(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("btc", "asset", state="bullish")
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    out = wm.query("btc")
    assert "[prediction]" in out
    assert "bullish" in out


def test_query_prediction_only_match_is_found(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_prediction("btc rises", "sell signal", 0.6, entity="btc")
    out = wm.query("btc")
    assert out != "No relevant information found."
    assert "sell signal" in out


# --- Bounded growth ---

def test_predictions_store_is_capped(tmp_path):
    wm = WorldModel(tmp_path)
    for i in range(PREDICTIONS_MAX + 5):
        wm.add_prediction(f"cond {i}", f"out {i}", 0.5)
    preds = _preds(wm)
    assert len(preds) == PREDICTIONS_MAX
    assert preds[0]["then"] == "out 5"  # oldest dropped


# --- Loop wiring: content_layer + engine ---

def test_content_layer_perceive_validates_predictions(engine):
    wm = engine.content_layer.world_model
    wm.add_prediction("btc is observed again", "bullish", 0.8, entity="btc")
    engine.perceive("btc update",
                    entities={"btc": {"type": "asset", "state": "bullish"}})
    assert _preds(wm, "btc")[0]["validated"] is True


def test_reflect_generates_then_next_perceive_validates(engine):
    wm = engine.content_layer.world_model
    engine.perceive("btc update",
                    entities={"btc": {"type": "asset", "state": "bullish"}})
    engine.reflect(world_state="btc update", confidence=0.7)
    pending = [p for p in _preds(wm, "btc") if "validated" not in p]
    assert pending, "reflect() PREDICT stage produced no persistence prediction"
    assert pending[0]["then"] == "bullish"
    # Next perception contradicts the prediction -> validated False.
    engine.perceive("btc update",
                    entities={"btc": {"type": "asset", "state": "bearish"}})
    flipped = [p for p in _preds(wm, "btc") if p.get("validated") is False]
    assert flipped, "next perceive() did not validate the pending prediction"
