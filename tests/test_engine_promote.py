# tests/test_engine_promote.py
import json

from conscio.agency.skills import SkillLibrary
from conscio.engine import ConsciousnessEngine
from conscio.noosphere import artifact, quarantine
from conscio.noosphere.paths import quarantine_db_path
from conscio.noosphere.quarantine import QuarantineRow

GOOD = [{"tool": "fs_write", "args": {"path": "t.txt", "content": "x"},
         "rationale": "w"},
        {"tool": "fs_read", "args": {"path": "t.txt"}, "rationale": "r"}]


def _seed(tmp_path, steps, *, status="quarantined", break_hash=False):
    body = artifact.build_body(goal_fp="fp", goal_text="demo",
                               tool_seq=[s["tool"] for s in steps],
                               plan_template=steps)
    blob = artifact.canonical_bytes(body)
    sha = artifact.content_hash(blob)
    row = QuarantineRow(
        content_sha256="WRONG" if break_hash else sha,
        origin_instance_id="o", origin_label="A", published_ts=1.0,
        importer_instance_id="i", imported_ts=2.0, goal_fp="fp",
        goal_text="demo", tool_seq=json.dumps([s["tool"] for s in steps]),
        plan_template=json.dumps(steps), artifact_json=blob,
        import_status=status, revalidation_result="ok",
        revalidation_error="", schema_version=1)
    quarantine.insert(quarantine_db_path(tmp_path), row)


def _record(tmp_path, rowid, *, passes=0, fails=0):
    qdb = quarantine_db_path(tmp_path)
    for _ in range(passes):
        quarantine.record_trial(qdb, rowid, passed=True, result="passed",
                                error="", ts=1.0)
    for _ in range(fails):
        quarantine.record_trial(qdb, rowid, passed=False, result="x",
                                error="", ts=1.0)


def _eng(tmp_path):
    return ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)


def _skill_count(tmp_path):
    lib = SkillLibrary(tmp_path / "conscio.db")
    try:
        return lib.count()
    finally:
        lib.close()


def test_disabled_refuses(tmp_path):
    _seed(tmp_path, GOOD)
    _record(tmp_path, 1, passes=3)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=False)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "enable-promote" in out.reason
    finally:
        eng.close()


def test_unknown_row_refuses(tmp_path):
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(42, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal" and "42" in out.reason
    finally:
        eng.close()


def test_non_quarantined_refuses(tmp_path):
    _seed(tmp_path, GOOD, status="rejected")
    _record(tmp_path, 1, passes=3)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "not quarantined" in out.reason
    finally:
        eng.close()


def test_already_promoted_refuses(tmp_path):
    _seed(tmp_path, GOOD)
    _record(tmp_path, 1, passes=3)
    quarantine.mark_promoted(quarantine_db_path(tmp_path), 1, ts=99.0,
                             skill_id=7)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "already promoted" in out.reason
    finally:
        eng.close()


def test_tamper_refuses_no_skill(tmp_path):
    _seed(tmp_path, GOOD, break_hash=True)
    _record(tmp_path, 1, passes=3)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "tampered" in out.reason
    finally:
        eng.close()
    assert _skill_count(tmp_path) == 0


def test_insufficient_trials_refuses(tmp_path):
    _seed(tmp_path, GOOD)
    _record(tmp_path, 1, passes=2)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "insufficient" in out.reason
    finally:
        eng.close()
    assert _skill_count(tmp_path) == 0


def test_failed_trial_refuses(tmp_path):
    _seed(tmp_path, GOOD)
    _record(tmp_path, 1, passes=3, fails=1)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "failed" in out.reason
    finally:
        eng.close()
    assert _skill_count(tmp_path) == 0


def test_unknown_tool_refuses(tmp_path):
    _seed(tmp_path, [{"tool": "nope_tool", "args": {}, "rationale": ""}])
    _record(tmp_path, 1, passes=3)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "nope_tool" in out.reason
    finally:
        eng.close()
    assert _skill_count(tmp_path) == 0


def test_happy_path_promotes_seeded(tmp_path):
    _seed(tmp_path, GOOD)
    _record(tmp_path, 1, passes=3)
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteResult"
        assert out.successes == 3 and out.failures == 0
        skill_id = out.skill_id
    finally:
        eng.close()
    lib = SkillLibrary(tmp_path / "conscio.db")
    try:
        rows = lib.all()
        assert len(rows) == 1
        assert rows[0]["id"] == skill_id
        assert rows[0]["successes"] == 3 and rows[0]["failures"] == 0
        assert rows[0]["goal_fp"] == "fp"
    finally:
        lib.close()
    row = quarantine.get(quarantine_db_path(tmp_path), 1)
    assert row.promoted_ts > 0 and row.promoted_skill_id == skill_id


def test_collision_refuses_and_not_marked(tmp_path):
    _seed(tmp_path, GOOD)
    _record(tmp_path, 1, passes=3)
    lib = SkillLibrary(tmp_path / "conscio.db")          # local owns the key
    try:
        lib.graft("fp", "local", json.dumps(["fs_write", "fs_read"]),
                  json.dumps(GOOD), successes=1, failures=0)
    finally:
        lib.close()
    eng = _eng(tmp_path)
    try:
        out = eng.promote_quarantined(1, enable_promote=True)
        assert out.__class__.__name__ == "PromoteRefusal"
        assert "collision" in out.reason
    finally:
        eng.close()
    row = quarantine.get(quarantine_db_path(tmp_path), 1)
    assert row.promoted_ts == 0                          # skip is not a promote
