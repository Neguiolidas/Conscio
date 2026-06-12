"""F2 immunity integration on the ActPipeline (skeptic, gating, L2)."""
import json

from conscio.agency.act import ActPipeline, ActStatus, goal_fingerprint
from conscio.agency.adapter import MockAdapter
from conscio.agency.breaker import CircuitBreaker
from conscio.agency.ledger import ActionLedger
from conscio.agency.skeptic import Skeptic
from conscio.agency.tools import Risk, ToolRegistry
from conscio.context_manager import ConsciousnessState

PROPOSAL = json.dumps({"tool": "noop", "args": {},
                       "rationale": "advance the goal",
                       "expected_outcome": "noop runs"})
CHECK_PASS = "A1: NO\nA2: NO\nA3: YES"
CHECK_FAIL = "A1: YES\nA2: NO\nA3: YES"


class _Trust:
    """Scriptable trust double."""
    def __init__(self, retries=3, level=1, fast=False):
        self.retries, self.level, self.fast = retries, level, fast
        self.successes = []

    def max_action_retries(self, task_type):
        return self.retries

    def autonomy_level(self, task_type):
        return self.level

    def fast_path_ok(self):
        return self.fast

    def on_success(self, task_type):
        self.successes.append(task_type)


class _Meta:
    def __init__(self):
        self.confidences, self.errors = [], []

    def record_confidence(self, task_type, confidence, outcome="pending"):
        self.confidences.append((task_type, confidence, outcome))

    def record_error(self, pattern):
        self.errors.append(pattern)


class _BusStub:
    def emit(self, **kw):
        pass

    def query(self, **kw):
        return []


def _registry(risk=Risk.MEDIUM):
    reg = ToolRegistry()
    reg.register("noop", lambda: "done", params={}, risk=risk,
                 description="no-op")
    return reg


def _pipeline(tmp_path, *, actor_script, skeptic_script=None,
              risk=Risk.MEDIUM, trust=None, meta=None, autonomy_cap=1):
    ledger = ActionLedger(tmp_path / "conscio.db")
    actor = MockAdapter(script=list(actor_script))
    skeptic = (Skeptic(MockAdapter(script=list(skeptic_script)))
               if skeptic_script is not None else None)
    pipe = ActPipeline(
        adapter=actor, registry=_registry(risk), ledger=ledger,
        breaker=CircuitBreaker(ledger, _BusStub(),
                               db_path=tmp_path / "conscio.db"),
        skeptic=skeptic, trust=trust, meta=meta, autonomy_cap=autonomy_cap)
    return pipe, ledger, actor


def _state(goals=("reduce dissonance",)):
    return ConsciousnessState(active_goals=list(goals))


# ── skeptic wiring ──────────────────────────────────────────────────────

def test_skeptic_pass_keeps_proposal_pending_at_l1(tmp_path):
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS])
    report = pipe.act(_state())
    assert report.status is ActStatus.PROPOSED
    assert report.verdict is not None and report.verdict.passed
    row = led.get(report.ledger_id)
    assert row["verdict"] == "PASS"
    led.close()


def test_skeptic_fail_rejects_and_counts_as_failure(tmp_path):
    meta = _Meta()
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_FAIL], meta=meta)
    report = pipe.act(_state())
    assert report.status is ActStatus.REJECTED
    assert not report.verdict.passed
    rows = led.latest(5)
    assert rows[0]["status"] == "failed"         # feeds the breaker
    assert rows[0]["verdict"] == "FAIL"
    assert any("skeptic_fail" in e for e in meta.errors)
    led.close()


def test_skeptic_call_is_clean_no_actor_leak(tmp_path):
    sk_adapter = MockAdapter(script=[CHECK_PASS])
    ledger = ActionLedger(tmp_path / "conscio.db")
    pipe = ActPipeline(
        adapter=MockAdapter(script=[PROPOSAL]), registry=_registry(),
        ledger=ledger,
        breaker=CircuitBreaker(ledger, _BusStub(),
                               db_path=tmp_path / "conscio.db"),
        skeptic=Skeptic(sk_adapter))
    pipe.act(_state(goals=("a very unique goal marker",)))
    audit_prompt = sk_adapter.calls[0]["prompt"]
    assert "volition of a persistent agent" not in audit_prompt
    assert "unique goal marker" not in audit_prompt   # only proposal + facts
    ledger.close()


# ── risk gating ─────────────────────────────────────────────────────────

def test_low_risk_fast_path_skips_audit(tmp_path):
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[],     # would raise if called
                             risk=Risk.LOW, trust=_Trust(fast=True))
    report = pipe.act(_state())
    assert report.status is ActStatus.PROPOSED
    assert report.verdict.audited is False
    assert led.get(report.ledger_id)["verdict"] == "unaudited"
    led.close()


def test_low_risk_without_calibration_still_audits(tmp_path):
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS],
                             risk=Risk.LOW, trust=_Trust(fast=False))
    report = pipe.act(_state())
    assert report.verdict.audited is True
    led.close()


def test_high_risk_never_auto_executes(tmp_path):
    trust = _Trust(level=2)
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS],
                             risk=Risk.HIGH, trust=trust, autonomy_cap=2)
    report = pipe.act(_state())
    assert report.status is ActStatus.PROPOSED   # human queue (R6)
    assert led.pending()                          # waiting for approve()
    led.close()


# ── L2 SUPERVISED ───────────────────────────────────────────────────────

def test_l2_executes_after_pass(tmp_path):
    meta = _Meta()
    trust = _Trust(level=2)
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS],
                             trust=trust, meta=meta, autonomy_cap=2)
    report = pipe.act(_state())
    assert report.status is ActStatus.EXECUTED
    assert report.result.ok and report.result.output == "done"
    assert led.get(report.ledger_id)["status"] == "executed"
    assert trust.successes == ["noop"]
    assert meta.confidences and meta.confidences[0][2] == "success"
    led.close()


def test_l2_capped_by_user_stays_proposed(tmp_path):
    trust = _Trust(level=2)
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS],
                             trust=trust, autonomy_cap=1)
    assert pipe.act(_state()).status is ActStatus.PROPOSED
    led.close()


def test_l2_not_earned_stays_proposed(tmp_path):
    trust = _Trust(level=1)
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS],
                             trust=trust, autonomy_cap=2)
    assert pipe.act(_state()).status is ActStatus.PROPOSED
    led.close()


# ── quarantine integration ──────────────────────────────────────────────

def test_quarantined_goal_is_skipped(tmp_path):
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL],
                             skeptic_script=[CHECK_PASS])
    goals = ["bad goal", "good goal"]
    fp_bad = goal_fingerprint("bad goal")
    for _ in range(3):
        led.record(goal_fp=fp_bad, tool="noop", args_json="{}",
                   rationale="", tier="T2", status="failed")
    pipe.breaker.trip(fp_bad, goal_text="bad goal")
    report = pipe.act(_state(goals=goals))
    assert report.status is ActStatus.PROPOSED
    assert led.get(report.ledger_id)["goal_fp"] == goal_fingerprint("good goal")
    led.close()


def test_all_goals_quarantined_fails_cleanly(tmp_path):
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL])
    fp = goal_fingerprint("only goal")
    for _ in range(3):
        led.record(goal_fp=fp, tool="noop", args_json="{}",
                   rationale="", tier="T2", status="failed")
    pipe.breaker.trip(fp, goal_text="only goal")
    report = pipe.act(_state(goals=["only goal"]))
    assert report.status is ActStatus.FAILED
    assert "quarantined" in report.reason
    led.close()


# ── F1 compatibility ────────────────────────────────────────────────────

def test_no_skeptic_no_trust_behaves_like_f1(tmp_path):
    pipe, led, _ = _pipeline(tmp_path, actor_script=[PROPOSAL])
    report = pipe.act(_state())
    assert report.status is ActStatus.PROPOSED   # stops at the human gate
    led.close()
