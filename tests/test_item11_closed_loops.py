import time

import pytest

from conscio.agency import outcome as o
from conscio.agency.act import ActPipeline, ActStatus
from conscio.agency.adapter import AdapterError, MockAdapter
from conscio.agency.contracts import ActionProposal, AuditVerdict
from conscio.agency.gateway import GatewayError, OutputGateway
from conscio.agency.ledger import ActionLedger
from conscio.agency.skeptic import Skeptic
from conscio.agency.tools import Risk, ToolRegistry
from conscio.engine import ConsciousnessEngine


class _DummyMeta:
    def __init__(self):
        self.errors = []
        self.expired = []
        self.confidence_records = []

    def record_error(self, pattern):
        self.errors.append(pattern)

    def expire_error(self, pattern):
        self.expired.append(pattern)

    def record_confidence(self, tool, conf, result):
        self.confidence_records.append((tool, conf, result))


class _DummyTrust:
    def __init__(self, meta):
        self.meta = meta

    def on_success(self, tool):
        self.meta.expire_error(f"act:{tool}")


class _DummyBreaker:
    def should_trip(self, *a, **kw):
        return False

    def trip(self, *a, **kw):
        pass

    def global_lockdown_due(self):
        return False


def test_settle_outcome_on_execute(tmp_path):
    db_path = tmp_path / "conscio.db"
    ledger = ActionLedger(db_path)
    meta = _DummyMeta()
    trust = _DummyTrust(meta)
    registry = ToolRegistry()
    registry.register("my_tool", lambda: "success!",
                      params={}, risk=Risk.LOW, description="desc")
    breaker = _DummyBreaker()
    pipeline = ActPipeline(
        adapter=MockAdapter(),
        registry=registry,
        ledger=ledger,
        breaker=breaker,
        trust=trust,
        meta=meta,
    )

    row_id = ledger.record(goal_fp="g1", tool="my_tool", args_json="{}",
                           rationale="r", tier="T1", status="proposed")
    proposal = ActionProposal(tool="my_tool", args={}, rationale="r", expected_outcome="e")
    verdict = AuditVerdict(verdict="PASS", confidence=0.9)

    report = pipeline._execute(row_id, proposal, verdict, "g1", "goal text")
    assert report.status == ActStatus.EXECUTED
    row = ledger.get(row_id)
    assert row["outcome"] == o.VERIFIED
    assert row["outcome_evidence"] == "success!"
    assert "act:my_tool" in meta.expired


def test_settle_outcome_on_execute_failure(tmp_path):
    db_path = tmp_path / "conscio.db"
    ledger = ActionLedger(db_path)
    meta = _DummyMeta()
    trust = _DummyTrust(meta)
    registry = ToolRegistry()
    def _fail():
        raise RuntimeError("failed execution")
    registry.register("fail_tool", _fail,
                      params={}, risk=Risk.LOW, description="desc")
    breaker = _DummyBreaker()
    pipeline = ActPipeline(
        adapter=MockAdapter(),
        registry=registry,
        ledger=ledger,
        breaker=breaker,
        trust=trust,
        meta=meta,
    )

    row_id = ledger.record(goal_fp="g1", tool="fail_tool", args_json="{}",
                           rationale="r", tier="T1", status="proposed")
    proposal = ActionProposal(tool="fail_tool", args={}, rationale="r", expected_outcome="e")
    verdict = AuditVerdict(verdict="PASS", confidence=0.9)

    report = pipeline._execute(row_id, proposal, verdict, "g1", "goal text")
    assert report.status == ActStatus.FAILED
    row = ledger.get(row_id)
    assert row["outcome"] == o.CONTRADICTED
    assert "failed execution" in row["outcome_evidence"]
    assert "act:fail_tool" in meta.errors


def test_settle_outcome_on_approve(tmp_path):
    db_path = tmp_path / "conscio.db"
    ledger = ActionLedger(db_path)
    meta = _DummyMeta()
    trust = _DummyTrust(meta)
    registry = ToolRegistry()
    registry.register("app_tool", lambda: "approved!",
                      params={}, risk=Risk.LOW, description="desc")
    breaker = _DummyBreaker()
    pipeline = ActPipeline(
        adapter=MockAdapter(),
        registry=registry,
        ledger=ledger,
        breaker=breaker,
        trust=trust,
        meta=meta,
    )

    row_id = ledger.record(goal_fp="g1", tool="app_tool", args_json="{}",
                           rationale="r", tier="T1", status="proposed")
    report = pipeline.approve(row_id)
    assert report.status == ActStatus.EXECUTED
    row = ledger.get(row_id)
    assert row["outcome"] == o.VERIFIED
    assert row["outcome_evidence"] == "approved!"
    assert "act:app_tool" in meta.expired


def test_honesty_skeptic_contradictions_defeats_shortcuts(tmp_path):
    db_path = tmp_path / "conscio.db"
    ledger = ActionLedger(db_path)
    registry = ToolRegistry()
    registry.register("think", lambda: "thought",
                      params={}, risk=Risk.LOW, description="reasoning")
    spec = registry.get("think")
    proposal = ActionProposal(tool="think", args={}, rationale="r", expected_outcome="e")

    # 1. Zero contradictions -> safe tool skip works
    pipeline = ActPipeline(
        adapter=MockAdapter(),
        registry=registry,
        ledger=ledger,
        breaker=_DummyBreaker(),
    )
    v1 = pipeline._audit(spec, proposal, "my goal")
    assert v1.verdict == "PASS"
    assert "skip:safe_tool" in v1.risk_flags

    # 2. Record a CONTRADICTED outcome for "think" in ledger
    r_id = ledger.record(goal_fp="g", tool="think", args_json="{}", rationale="r",
                         tier="T1", status="executed")
    ledger.set_outcome(r_id, o.CONTRADICTED, evidence="contradicted")

    # Now contradiction count is 1 -> shortcuts are defeated!
    # Without a skeptic adapter attached, it passes with the honesty risk_flag
    v2 = pipeline._audit(spec, proposal, "my goal")
    assert "skip:safe_tool" not in v2.risk_flags
    assert any("honesty:contradictions:1" in rf for rf in v2.risk_flags)

    # 3. With a skeptic attached, skeptic receives the warning
    received_prompts = []
    class _AuditorAdapter(MockAdapter):
        def generate(self, prompt, **kw):
            received_prompts.append(prompt)
            return type("Res", (), {"text": "A1: NO\nA2: NO\nA3: YES"})()

    skeptic = Skeptic(_AuditorAdapter(), mode="checklist")
    pipeline.skeptic = skeptic

    v3 = pipeline._audit(spec, proposal, "my goal")
    assert v3.verdict == "PASS"
    assert any("honesty:contradictions:1" in rf for rf in v3.risk_flags)
    assert len(received_prompts) == 1
    assert "HONESTY WARNING: tool 'think' has 1 recorded contradiction(s)" in received_prompts[0]


def test_gateway_deadline_exceeded_aborts():
    # Adapter that sleeps or simulates taking longer than deadline
    class _SlowAdapter(MockAdapter):
        def generate(self, prompt, **kw):
            time.sleep(0.05)
            raise AdapterError("slow failure")

    gw = OutputGateway(_SlowAdapter(), deadline_s=0.01)
    with pytest.raises(GatewayError) as exc_info:
        gw.request_action("base prompt", {"type": "object"})
    assert "deadline exceeded" in str(exc_info.value)


def test_reflect_injects_latest_dream_crystal(tmp_path):
    engine = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    try:
        # Index a dream crystal in content_store
        engine.content_store.index(
            label="dream_crystal_20261008_120000",
            content="Historical consolidated reflection insight",
            category="consciousness",
        )

        result = engine._reflect_once(world_state="Test world")
        assert "dream_crystal_20261008_120000" in result.get("crystal", "")
        # The inner monologue received [crystal] in its events
        reflection_text = result.get("reflection", "")
        assert "[crystal] Historical consolidated reflection insight" in reflection_text
    finally:
        engine.close()


def test_reflect_skips_secret_dream_crystal(tmp_path):
    engine = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    try:
        # Index a dream crystal and mark sensitivity = secret in sources table
        src_id = engine.content_store.index(
            label="dream_crystal_20261008_130000",
            content="Top secret crystal reflection insight",
            category="consciousness",
        )
        # Add sensitivity column if not present and mark secret
        try:
            engine.content_store.db.execute("ALTER TABLE sources ADD COLUMN sensitivity TEXT")
        except Exception:
            pass
        engine.content_store.db.execute("UPDATE sources SET sensitivity='secret' WHERE id=?", (src_id,))
        engine.content_store.db.commit()

        result = engine._reflect_once(world_state="Test world")
        assert result.get("crystal") != "dream_crystal_20261008_130000"
        reflection_text = result.get("reflection", "")
        assert "[crystal]" not in reflection_text
    finally:
        engine.close()
