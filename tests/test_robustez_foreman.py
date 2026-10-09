"""Unit tests for the 4 Foreman robustez modules ported to Conscio stdlib.

Modules tested:
1. verifier
2. arbiter
3. stateledger
4. llmretry
"""

import time

import pytest

from conscio.robustez import (
    arbiter,
    llmretry,
    stateledger,
    verifier,
)


# ============================================================================
# 1. verifier
# ============================================================================
def test_verifier_gate_distinction():
    # Broken gate (syntax/setup failure) -> gate_invalid
    broken_gate = verifier.GateResult("cmd", False, "sh: line 1: fakecmd: command not found")
    assert verifier.is_gate_invalid(broken_gate) is True

    # Real test failure (assertion error) -> valid gate failure
    real_failure = verifier.GateResult("test", False, "AssertionError: 2 != 3")
    assert verifier.is_gate_invalid(real_failure) is False

    # Passed gate is never invalid
    passed_gate = verifier.GateResult("ok", True)
    assert verifier.is_gate_invalid(passed_gate) is False


def test_verifier_gates_before_judge():
    # If objective gate fails, verification fails regardless of judge verdict
    checks = [
        lambda: verifier.GateResult("lint", True),
        lambda: verifier.GateResult("unit_tests", False, "AssertionError: failed"),
    ]
    judge_called = False

    def mock_judge(results):
        nonlocal judge_called
        judge_called = True
        return True, "Judge says looks good anyway"

    verdict = verifier.verify(checks, judge=mock_judge)
    assert verdict.passed is False
    assert verdict.gates_green is False
    assert judge_called is True  # Judge still provides feedback


def test_verifier_judge_exception_handled():
    checks = [lambda: verifier.GateResult("tests", True)]

    def broken_judge(results):
        raise RuntimeError("LLM Judge crashed")

    verdict = verifier.verify(checks, judge=broken_judge)
    assert verdict.passed is False
    assert "Judge execution failed" in verdict.feedback


def test_verifier_all_green():
    checks = [
        lambda: verifier.GateResult("lint", True),
        lambda: verifier.GateResult("tests", True),
    ]
    verdict = verifier.verify(checks)
    assert verdict.passed is True
    assert verdict.gates_green is True
    assert verdict.gate_invalid is False


# ============================================================================
# 2. arbiter
# ============================================================================
def test_arbiter_single_appeal_and_non_disputable():
    arb = arbiter.Arbiter()
    arb.reset_appeals()

    failed_verdict = verifier.Verdict(passed=False, reason="Objective tests failed", feedback="", gate_invalid=False, gates_green=False)

    # Objective gate failure is non-disputable -> uphold without calling judge
    ruling = arb.arbitrate({"task": "build"}, failed_verdict, lambda p: "evidence", task_id="task_1")
    assert ruling.upheld is True
    assert ruling.overturned is False
    assert "non-disputable" in ruling.reason.lower()

    # Second appeal on same task fails immediately
    ruling2 = arb.arbitrate({"task": "build"}, failed_verdict, lambda p: "evidence", task_id="task_1")
    assert ruling2.upheld is True
    assert "limit" in ruling2.reason.lower()


def test_arbiter_overturn_with_fresh_evidence():
    arb = arbiter.Arbiter()
    arb.reset_appeals()

    # Gates were green, but judge rejected
    green_verdict = verifier.Verdict(passed=False, reason="Subjective review failed", feedback="", gate_invalid=False, gates_green=True)

    read_calls = []

    def mock_read(path):
        read_calls.append(path)
        return "Clean code diff"

    def mock_judge(prompt):
        return {"ruling": "overturn", "reason": "Evidence shows compliant behavior"}

    ruling = arb.arbitrate(
        {"task": "style", "evidence_path": "diff.patch"},
        green_verdict,
        read_evidence=mock_read,
        task_id="task_2",
        judge=mock_judge,
    )
    assert ruling.overturned is True
    assert ruling.upheld is False
    assert "compliant" in ruling.reason


def test_arbiter_fail_closed_on_invalid_judge():
    arb = arbiter.Arbiter()
    green_verdict = verifier.Verdict(passed=False, reason="Rejected", feedback="", gate_invalid=False, gates_green=True)

    # Judge returns invalid output
    ruling1 = arb.arbitrate({}, green_verdict, lambda p: "", judge=lambda p: "invalid_type")
    assert ruling1.upheld is True

    # Judge raises exception
    def error_judge(p):
        raise ValueError("judge error")

    ruling2 = arb.arbitrate({}, green_verdict, lambda p: "", judge=error_judge)
    assert ruling2.upheld is True
    assert "failed" in ruling2.reason.lower()


# ============================================================================
# 3. stateledger
# ============================================================================
def test_stateledger_lifecycle(tmp_path):
    db_file = tmp_path / "ledger.sqlite"
    states = {"pending", "running", "completed", "failed", "blocked"}
    transitions = {
        "pending": {"running"},
        "running": {"completed", "failed", "pending", "blocked"},
        "failed": {"running", "blocked", "pending"},
        "blocked": {"pending"},
    }
    ledger = stateledger.StateLedger(db_file, states, transitions, initial="pending", max_retries=2)

    ledger.add_item("item1", {"payload": "data"})

    # Worker 1 claims
    item = ledger.claim_next("worker1", lease_seconds=10)
    assert item is not None
    assert item["item_id"] == "item1"
    assert item["owner"] == "worker1"

    # Worker 2 tries to claim same item -> returns None
    assert ledger.claim_next("worker2", lease_seconds=10) is None

    # Invalid transition raises TransitionError
    with pytest.raises(stateledger.TransitionError):
        ledger.transition("item1", "archived", worker_id="worker1")

    # Wrong worker raises PermissionError
    with pytest.raises(PermissionError):
        ledger.transition("item1", "completed", worker_id="worker2")

    # Valid transition
    assert ledger.transition("item1", "completed", worker_id="worker1") is True


def test_stateledger_reclaim_and_revive(tmp_path):
    db_file = tmp_path / "ledger_exp.sqlite"
    states = {"pending", "running", "blocked"}
    transitions = {
        "pending": {"running"},
        "running": {"pending", "blocked"},
        "blocked": {"pending"},
    }
    ledger = stateledger.StateLedger(db_file, states, transitions, initial="pending", max_retries=1)

    ledger.add_item("itemX", "payload")
    # Claim 1: attempts = 1
    ledger.claim_next("w1", lease_seconds=1)

    # Reclaim with simulated past timestamp
    now = time.time() + 10
    reclaimed = ledger.reclaim_expired(now=now)
    assert "itemX" in reclaimed

    # Item exceeded max_retries (1) -> moved to blocked
    assert ledger.claim_next("w1", lease_seconds=1) is None

    # Revive by operator
    assert ledger.revive_blocked("itemX") is True
    # Can now be claimed again
    item_again = ledger.claim_next("w1", lease_seconds=10)
    assert item_again is not None
    assert item_again["item_id"] == "itemX"


def test_stateledger_release(tmp_path):
    db_file = tmp_path / "ledger_rel.sqlite"
    states = {"pending", "running", "blocked"}
    transitions = {
        "pending": {"running"},
        "running": {"pending", "blocked"},
        "blocked": {"pending"},
    }
    ledger = stateledger.StateLedger(db_file, states, transitions, initial="pending")
    ledger.add_item("itemR", "data")
    it = ledger.claim_next("w1", 10)
    assert it is not None

    # Release returns item to pending
    ledger.release("itemR", "w1")
    it2 = ledger.claim_next("w2", 10)
    assert it2 is not None
    assert it2["owner"] == "w2"


# ============================================================================
# 4. llmretry
# ============================================================================
def test_llmretry_transient_and_fallback():
    calls = []

    def mock_call(item, **kwargs):
        calls.append(item)
        if item == "provider_a":
            # Transient error
            raise ConnectionError("Connection reset by peer")
        return f"result_from_{item}"

    sleeps = []

    res = llmretry.call_with_fallback(
        mock_call,
        chain=["provider_a", "provider_b"],
        sleep=lambda s: sleeps.append(s),
    )
    assert res == "result_from_provider_b"
    # provider_a was retried 3 times (with backoff 1, 2, 4s), then fell back to provider_b
    assert calls.count("provider_a") == 4  # 1 initial + 3 retries
    assert "provider_b" in calls


def test_llmretry_rate_limit_with_hint():
    calls = 0
    waits = []

    class RateLimitWithHint(Exception):
        status_code = 429
        def __str__(self):
            return "Rate limit: retry in 3.5s"

    def mock_call(item, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RateLimitWithHint()
        return "ok"

    res = llmretry.call_with_fallback(
        mock_call,
        chain=["provider_x"],
        sleep=lambda s: waits.append(s),
    )
    assert res == "ok"
    assert len(waits) == 2
    assert waits[0] == 3.5


def test_llmretry_quota_exhaustion_immediate_fallback():
    fallback_events = []

    class QuotaError(Exception):
        status_code = 402

    def mock_call(item, **kwargs):
        if item == "p1":
            raise QuotaError("insufficient_quota")
        return "p2_ok"

    def on_fallback(old_p, new_p, exc):
        fallback_events.append((old_p, new_p))

    res = llmretry.call_with_fallback(
        mock_call,
        chain=["p1", "p2"],
        on_fallback=on_fallback,
    )
    assert res == "p2_ok"
    assert fallback_events == [("p1", "p2")]


def test_llmretry_unrecoverable_error_raises_immediately():
    class BadRequestError(Exception):
        status_code = 400

    def mock_call(item, **kwargs):
        raise BadRequestError("Bad Request")

    with pytest.raises(BadRequestError):
        llmretry.call_with_fallback(
            mock_call,
            chain=["provider_a", "provider_b"],
        )
