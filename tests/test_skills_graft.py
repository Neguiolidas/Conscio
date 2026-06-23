# tests/test_skills_graft.py
import json

from conscio.agency.skills import SkillLibrary


def _lib(tmp_path):
    return SkillLibrary(tmp_path / "conscio.db")


def test_graft_inserts_with_seeded_counters(tmp_path):
    lib = _lib(tmp_path)
    try:
        sid = lib.graft(
            "fp1", "demo", json.dumps(["fs_read"]),
            json.dumps([{"tool": "fs_read", "args": {}, "rationale": ""}]),
            successes=3, failures=0)
        assert isinstance(sid, int) and sid > 0
        rows = lib.all()
        assert len(rows) == 1
        assert rows[0]["successes"] == 3 and rows[0]["failures"] == 0
        assert rows[0]["goal_fp"] == "fp1"
    finally:
        lib.close()


def test_graft_conflict_returns_none_and_keeps_original(tmp_path):
    lib = _lib(tmp_path)
    try:
        seq = json.dumps(["fs_read"])
        tpl = json.dumps([{"tool": "fs_read", "args": {}, "rationale": ""}])
        first = lib.graft("fp1", "demo", seq, tpl, successes=3, failures=0)
        assert first is not None
        second = lib.graft("fp1", "other", seq, tpl, successes=9, failures=9)
        assert second is None                       # conflict: no overwrite
        rows = lib.all()
        assert len(rows) == 1
        assert rows[0]["successes"] == 3            # original untouched
        assert rows[0]["goal_text"] == "demo"
    finally:
        lib.close()


def test_graft_seeded_below_floor_not_served(tmp_path):
    # Defense in depth: a grafted skill whose seeded rate < MIN_SERVE_RATE is
    # benched by the existing serve-gate.
    lib = _lib(tmp_path)
    try:
        lib.graft(
            "fp1", "do the thing", json.dumps(["fs_read"]),
            json.dumps([{"tool": "fs_read", "args": {}, "rationale": ""}]),
            successes=1, failures=5)
        assert lib.few_shot("do the thing") == []
    finally:
        lib.close()
