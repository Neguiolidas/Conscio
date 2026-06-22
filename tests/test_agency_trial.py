# tests/test_agency_trial.py
from pathlib import Path

from conscio.agency import trial
from conscio.agency.contracts import AuditVerdict
from conscio.agency.tools import Risk, make_default_registry


class _Skeptic:
    """Stub: PASS unless told to FAIL; records every audited tool."""
    def __init__(self, ok=True):
        self.ok = ok
        self.seen = []

    def audit(self, proposal, *, goal_text):
        self.seen.append(proposal.tool)
        return AuditVerdict(verdict="PASS" if self.ok else "FAIL",
                            reasons=[] if self.ok else ["nope"])


def _fs_registry(tmp):
    return make_default_registry(sandbox_root=tmp, content_store=None,
                                 event_bus=None, goal_generator=None)


def test_self_contained_plan_passes(tmp_path):
    reg = _fs_registry(tmp_path)
    steps = [
        {"tool": "fs_write", "args": {"path": "t.txt", "content": "hi"},
         "rationale": "write"},
        {"tool": "fs_read", "args": {"path": "t.txt"}, "rationale": "read"},
    ]
    out = trial.run_trial(steps, goal_text="demo",
                          skeptic=_Skeptic(ok=True), registry=reg)
    assert out.passed is True and out.result == "passed"
    assert [s.stage for s in out.steps] == ["ok", "ok"]


def test_unknown_tool_fails_and_stops(tmp_path):
    reg = _fs_registry(tmp_path)
    spy = _Skeptic(ok=True)
    steps = [{"tool": "nope", "args": {}, "rationale": ""},
             {"tool": "fs_write", "args": {"path": "x", "content": "y"},
              "rationale": ""}]
    out = trial.run_trial(steps, goal_text="g", skeptic=spy, registry=reg)
    assert out.passed is False and out.result == "unknown_tool:nope"
    assert len(out.steps) == 1                # stopped at step 1
    assert spy.seen == []                     # skeptic never reached


def test_invalid_args_fails(tmp_path):
    reg = _fs_registry(tmp_path)
    steps = [{"tool": "fs_write", "args": {"path": "x"}, "rationale": ""}]
    out = trial.run_trial(steps, goal_text="g", skeptic=_Skeptic(),
                          registry=reg)
    assert out.passed is False and out.result == "invalid_args:fs_write"


def test_precheck_sandbox_escape_fails(tmp_path):
    reg = _fs_registry(tmp_path)
    steps = [{"tool": "fs_write",
              "args": {"path": "../escape.txt", "content": "x"},
              "rationale": ""}]
    out = trial.run_trial(steps, goal_text="g", skeptic=_Skeptic(),
                          registry=reg)
    assert out.passed is False and out.result == "precheck:fs_write"


def test_skeptic_reject_fails_without_dispatch(tmp_path):
    reg = _fs_registry(tmp_path)
    steps = [{"tool": "fs_write", "args": {"path": "t.txt", "content": "x"},
              "rationale": ""}]
    out = trial.run_trial(steps, goal_text="g", skeptic=_Skeptic(ok=False),
                          registry=reg)
    assert out.passed is False and out.result == "skeptic_reject:fs_write"
    # dispatch never ran -> the sandbox file must not exist
    assert not (Path(tmp_path) / "t.txt").exists()


def test_exec_fail_on_missing_read(tmp_path):
    reg = _fs_registry(tmp_path)
    steps = [{"tool": "fs_read", "args": {"path": "absent.txt"},
              "rationale": ""}]
    out = trial.run_trial(steps, goal_text="g", skeptic=_Skeptic(),
                          registry=reg)
    assert out.passed is False and out.result == "exec_fail:fs_read"


def test_high_risk_blocked_without_dispatch():
    # Inject a registry whose tool is HIGH risk; the rule must block it
    # before dispatch regardless of registry contents.
    from conscio.agency.tools import ToolRegistry
    reg = ToolRegistry()
    dispatched = []
    reg.register("danger", lambda **kw: dispatched.append(kw) or "x",
                 params={}, risk=Risk.HIGH, description="d")
    out = trial.run_trial([{"tool": "danger", "args": {}, "rationale": ""}],
                          goal_text="g", skeptic=_Skeptic(), registry=reg)
    assert out.passed is False and out.result == "high_risk_blocked:danger"
    assert dispatched == []                    # never executed


def test_empty_plan_fails():
    from conscio.agency.tools import ToolRegistry
    out = trial.run_trial([], goal_text="g", skeptic=_Skeptic(),
                          registry=ToolRegistry())
    assert out.passed is False
