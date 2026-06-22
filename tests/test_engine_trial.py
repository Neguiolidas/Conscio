# tests/test_engine_trial.py
import json
import os
import types

from conscio.agency.contracts import AuditVerdict
from conscio.agency.ledger import ActionLedger
from conscio.agency.skills import SkillLibrary
from conscio.engine import ConsciousnessEngine
from conscio.noosphere import artifact, quarantine
from conscio.noosphere.paths import quarantine_db_path
from conscio.noosphere.quarantine import QuarantineRow


class _Skeptic:
    def __init__(self, ok=True):
        self.ok = ok

    def audit(self, proposal, *, goal_text):
        return AuditVerdict(verdict="PASS" if self.ok else "FAIL",
                            reasons=[] if self.ok else ["nope"])


class _Noop:
    def close(self):
        pass


def _engine(tmp_path, skeptic=None):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    if skeptic is not None:
        # Stub the pipeline; give ledger/trust/breaker no-op closers so close()
        # teardown is safe even if it calls .close() on them.
        eng._act_pipeline = types.SimpleNamespace(
            skeptic=skeptic, ledger=_Noop(), trust=_Noop(), breaker=_Noop())
    return eng


def _seed(tmp_path, steps, *, status="quarantined", break_hash=False,
          plan_template_override=None):
    body = artifact.build_body(goal_fp="fp", goal_text="demo",
                               tool_seq=[s["tool"] for s in steps],
                               plan_template=steps)
    blob = artifact.canonical_bytes(body)
    sha = artifact.content_hash(blob)
    row = QuarantineRow(
        content_sha256="WRONG" if break_hash else sha,
        origin_instance_id="o", origin_label="A", published_ts=1.0,
        importer_instance_id="i", imported_ts=2.0, goal_fp="fp",
        goal_text="demo",
        tool_seq=json.dumps([s["tool"] for s in steps]),
        plan_template=(plan_template_override if plan_template_override
                       is not None else json.dumps(steps)),
        artifact_json=blob, import_status=status, revalidation_result="ok",
        revalidation_error="", schema_version=1)
    quarantine.insert(quarantine_db_path(tmp_path), row)


def test_disabled_refuses(tmp_path):
    eng = _engine(tmp_path, skeptic=_Skeptic())
    try:
        out = eng.trial_quarantined(1, enable_trial=False)
        assert out.__class__.__name__ == "TrialRefusal"
        assert "enable-trial" in out.reason
    finally:
        eng.close()


def test_no_adapter_refuses(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    try:
        out = eng.trial_quarantined(1, enable_trial=True)
        assert out.__class__.__name__ == "TrialRefusal"
        assert "adapter" in out.reason
    finally:
        eng.close()


def test_unknown_row_refuses(tmp_path):
    eng = _engine(tmp_path, skeptic=_Skeptic())
    try:
        out = eng.trial_quarantined(42, enable_trial=True)
        assert out.__class__.__name__ == "TrialRefusal" and "42" in out.reason
    finally:
        eng.close()


def test_non_quarantined_refuses(tmp_path):
    _seed(tmp_path, [{"tool": "fs_write", "args": {"path": "t", "content": "x"},
                      "rationale": ""}], status="rejected")
    eng = _engine(tmp_path, skeptic=_Skeptic())
    try:
        out = eng.trial_quarantined(1, enable_trial=True)
        assert out.__class__.__name__ == "TrialRefusal"
        assert "not quarantined" in out.reason
    finally:
        eng.close()


def test_corrupt_plan_refuses_without_count(tmp_path):
    # plan_template valid JSON but not a list -> refuse, no count bump.
    _seed(tmp_path, [{"tool": "fs_write", "args": {"path": "t", "content": "x"},
                      "rationale": ""}], plan_template_override='{"not":"list"}')
    eng = _engine(tmp_path, skeptic=_Skeptic())
    try:
        out = eng.trial_quarantined(1, enable_trial=True)
        assert out.__class__.__name__ == "TrialRefusal"
    finally:
        eng.close()
    row = quarantine.get(quarantine_db_path(tmp_path), 1)
    assert row.last_trial_result == "corrupt_plan"
    assert row.trial_successes == 0 and row.trial_failures == 0


def test_tamper_refuses_without_count(tmp_path):
    _seed(tmp_path, [{"tool": "fs_write", "args": {"path": "t", "content": "x"},
                      "rationale": ""}], break_hash=True)
    eng = _engine(tmp_path, skeptic=_Skeptic())
    try:
        out = eng.trial_quarantined(1, enable_trial=True)
        assert out.__class__.__name__ == "TrialRefusal"
    finally:
        eng.close()
    row = quarantine.get(quarantine_db_path(tmp_path), 1)
    assert row.last_trial_result == "tampered"
    assert row.trial_successes == 0 and row.trial_failures == 0


def test_good_plan_passes_and_records(tmp_path):
    _seed(tmp_path, [
        {"tool": "fs_write", "args": {"path": "t.txt", "content": "hi"},
         "rationale": "w"},
        {"tool": "fs_read", "args": {"path": "t.txt"}, "rationale": "r"}])
    eng = _engine(tmp_path, skeptic=_Skeptic(ok=True))
    try:
        out = eng.trial_quarantined(1, enable_trial=True)
        assert out.passed is True
    finally:
        eng.close()
    row = quarantine.get(quarantine_db_path(tmp_path), 1)
    assert row.trial_successes == 1 and row.trial_failures == 0


def test_bad_plan_fails_and_records(tmp_path):
    _seed(tmp_path, [{"tool": "fs_read", "args": {"path": "absent.txt"},
                      "rationale": ""}])
    eng = _engine(tmp_path, skeptic=_Skeptic(ok=True))
    try:
        out = eng.trial_quarantined(1, enable_trial=True)
        assert out.passed is False and out.result == "exec_fail:fs_read"
    finally:
        eng.close()
    row = quarantine.get(quarantine_db_path(tmp_path), 1)
    assert row.trial_failures == 1 and row.trial_successes == 0


def test_isolation_live_db_untouched(tmp_path):
    # A failing trial must not write the agent's ledger/skills.
    _seed(tmp_path, [{"tool": "fs_read", "args": {"path": "absent.txt"},
                      "rationale": ""}])
    eng = _engine(tmp_path, skeptic=_Skeptic(ok=True))
    try:
        eng.trial_quarantined(1, enable_trial=True)
    finally:
        eng.close()
    db = tmp_path / "conscio.db"
    led = ActionLedger(db)
    try:
        assert led.pending(200) == []                 # no proposed rows
    finally:
        led.close()
    lib = SkillLibrary(db)
    try:
        assert lib.count() == 0                        # no skills created
    finally:
        lib.close()


def test_sandbox_dir_removed(tmp_path, monkeypatch):
    import tempfile as _t
    created = {}
    real = _t.mkdtemp

    def spy(*a, **k):
        path = real(*a, **k)
        created["path"] = path
        return path
    monkeypatch.setattr(_t, "mkdtemp", spy)
    _seed(tmp_path, [{"tool": "fs_write", "args": {"path": "t.txt",
                      "content": "x"}, "rationale": ""}])
    eng = _engine(tmp_path, skeptic=_Skeptic(ok=True))
    try:
        eng.trial_quarantined(1, enable_trial=True)
    finally:
        eng.close()
    assert "path" in created and not os.path.exists(created["path"])
