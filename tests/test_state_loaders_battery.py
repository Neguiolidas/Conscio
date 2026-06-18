# tests/test_state_loaders_battery.py
"""v1.9 deep battery — JSON state LOADERS survive a binary / wrong-type file.

B-013 (B-005/B-008 class recurrence): several loaders caught only
       `json.JSONDecodeError` (or `(OSError, json.JSONDecodeError)`), but a
       binary / non-UTF-8 file makes the read raise `UnicodeDecodeError` — a
       ValueError that is NOT a JSONDecodeError — which escaped and crashed the
       load. `goal_generator._load` + `auto_evolution._load` run inside
       `engine.__init__`, so a corrupt goals.json / evolution_proposals.json
       crashed engine CONSTRUCTION (I-S4), same failure mode as B-008.

Fix: broaden to ValueError (covers JSONDecodeError + UnicodeDecodeError) + a
type guard, so a corrupt/legacy file degrades to the empty default.
"""
from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine
from conscio.structural_consent import StructuralConsent
from conscio.structural_drift import StructuralDriftStore


def _engine(d):
    e = ConsciousnessEngine("glm-5.1", storage_path=d)
    e.content_layer._session_rag = _RAG_DISABLED       # hermetic: no probe
    return e


# ── engine-construction-critical loaders ──────────────────────────────────────
def test_try_break_binary_goals_file_constructs(tmp_path):
    (tmp_path / "goals.json").write_bytes(b"\xff\xfe\x00 not json")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert isinstance(eng.advisory(), dict)
        assert eng.goals.active_goals() == []           # degraded to empty
    finally:
        eng.close()


def test_try_break_nonlist_goals_file_constructs(tmp_path):
    (tmp_path / "goals.json").write_text('{"a": 1}')    # valid JSON, wrong type
    eng = _engine(tmp_path)
    try:
        assert isinstance(eng.advisory(), dict)
    finally:
        eng.close()


def test_try_break_binary_evolution_file_constructs(tmp_path):
    (tmp_path / "evolution_proposals.json").write_bytes(b"\xff\xfe binary \x00")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert eng.evolution.pending_proposals() == []
    finally:
        eng.close()


# ── structural state loaders ──────────────────────────────────────────────────
def test_try_break_binary_consent_file(tmp_path):
    p = tmp_path / "consent.json"
    p.write_bytes(b"\xff\xfe binary consent \x00")
    c = StructuralConsent(p)                             # must NOT raise
    assert c.scope_for("ws_anything") is not None        # default scope


def test_try_break_binary_drift_store(tmp_path):
    p = tmp_path / "drift.json"
    p.write_bytes(b"\xff\xfe binary drift \x00")
    s = StructuralDriftStore(p)                          # must NOT raise
    assert s._map == {}                                  # degraded to empty


# ── I-S4 capstone: every persistent state file corrupt at once ────────────────
def test_try_break_all_state_files_corrupt_constructs(tmp_path):
    """No single loader's failure cascades: EVERY known persistent file binary
    at once → engine still constructs and advisory() works (B-006 + B-008 +
    B-011 + B-013 together)."""
    for name in ("conscio.db", "state_summary.json", "state_summary.txt",
                 "world_model.json", "meta_cognition.json", "goals.json",
                 "evolution_proposals.json"):
        (tmp_path / name).write_bytes(b"\xff\xfe\x00 garbage not valid json \xff")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert isinstance(eng.advisory(), dict)
        assert eng.goals.active_goals() == []
        assert eng.evolution.pending_proposals() == []
    finally:
        eng.close()
