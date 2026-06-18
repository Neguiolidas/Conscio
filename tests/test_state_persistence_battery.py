# tests/test_state_persistence_battery.py
"""v1.9 deep battery — JSON-backed state stores survive a valid-but-INCOMPLETE
file (schema drift across versions), not only outright corruption.

B-011: safe_read_json (B-008) guarantees a dict OR None, but a VALID dict missing
       expected keys — a legacy/migrated world_model.json / meta_cognition.json
       written before `predictions` / `error_patterns` / `prediction_log` existed,
       or a hand-edited `{}` — was returned as-is. Construction succeeded; the
       first method touching a missing key (`self._data["entities"]`,
       `["predictions"]`, `["confidence_history"]`, ...) raised KeyError. _load
       now merges the loaded data over the full default skeleton (durable guard
       read_json_dict), so every required key is always present.
"""
import json

from conscio.meta_cognition import MetaCognition
from conscio.world_model import WorldModel


# ── WorldModel ────────────────────────────────────────────────────────────────
def test_try_break_world_model_incomplete_json(tmp_path):
    # legacy file: only `entities`; missing relations/predictions/prediction_log
    (tmp_path / "world_model.json").write_text(json.dumps(
        {"entities": {"bot": {"type": "system", "state": "up",
                              "relevance": 1.0,
                              "last_updated": "2026-06-18T08:00:00"}}}))
    wm = WorldModel(tmp_path)
    wm.add_relation("bot", "owns", "wallet")     # touched ["relations"]
    wm.add_prediction("if x", "then y", 0.5)     # touched ["predictions"]
    assert wm.get_entity("bot") is not None       # loaded data preserved
    assert wm.status()["relations"] >= 1


def test_try_break_world_model_empty_dict_json(tmp_path):
    (tmp_path / "world_model.json").write_text("{}")
    wm = WorldModel(tmp_path)
    wm.add_entity("a", "system", state="ok")     # touched ["entities"]
    assert wm.get_entity("a") is not None
    assert wm.to_dict()["entities"]


# ── MetaCognition ─────────────────────────────────────────────────────────────
def test_try_break_meta_cognition_incomplete_json(tmp_path):
    # legacy file: only confidence_history; missing the other three keys
    (tmp_path / "meta_cognition.json").write_text(json.dumps(
        {"confidence_history": []}))
    mc = MetaCognition(tmp_path)
    mc.record_error("timeout")                   # touched ["error_patterns"]
    mc.add_critique("t", "did", "should")        # touched ["self_critiques"]
    st = mc.status()                             # touched blind_spots etc.
    assert st["error_patterns"] >= 1
    assert st["critiques"] >= 1


def test_try_break_meta_cognition_empty_dict_json(tmp_path):
    (tmp_path / "meta_cognition.json").write_text("{}")
    mc = MetaCognition(tmp_path)
    mc.record_confidence("plan", 0.7)            # touched ["confidence_history"]
    assert mc.status()["confidence_records"] >= 1
