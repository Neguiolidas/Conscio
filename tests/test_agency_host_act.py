# tests/test_agency_host_act.py
from conscio.agency.host_act import HostActChannel
from conscio.agency.ledger import ActionLedger
from conscio.agency.contracts import AuditVerdict
from conscio.agency.tools import registry_from_manifest


class FakeSkeptic:
    def __init__(self, verdict="PASS"):
        self._v = verdict

    def audit(self, proposal, *, goal_text=""):
        return AuditVerdict(verdict=self._v,
                            reasons=([] if self._v == "PASS" else ["nope"]),
                            risk_flags=[], confidence=0.8)


class FakeBreaker:
    def __init__(self, lockdown=False):
        self._lock = lockdown
        self.trips = []

    def global_lockdown_due(self):
        return self._lock

    def should_trip(self, goal_fp, *, task_type=""):
        return True

    def trip(self, goal_fp, *, detail="", goal_text=""):
        self.trips.append((goal_fp, detail))


class FakeTrust:
    def __init__(self):
        self.successes = []

    def on_success(self, tool):
        self.successes.append(tool)


def _manifest(risk="low", policy="auto"):
    return [{"name": "deploy",
             "params": {"env": {"type": "str", "required": True}},
             "risk": risk, "approval_policy": policy}]


def _intent(env="prod", **kw):
    base = {"tool": "deploy", "args": {"env": env},
            "rationale": "ship", "expected_outcome": "live"}
    base.update(kw)
    return base


def _chan(tmp_path, *, risk="low", policy="auto", verdict="PASS",
          awake=True, lockdown=False, events=None):
    led = ActionLedger(tmp_path / "conscio.db")
    ev = events if events is not None else []
    chan = HostActChannel(
        ledger=led, skeptic=FakeSkeptic(verdict), breaker=FakeBreaker(lockdown),
        trust=FakeTrust(),
        registry=registry_from_manifest(_manifest(risk, policy)),
        emit_fn=lambda **kw: ev.append(kw), awake_fn=lambda: awake)
    return chan, led


# ── Task 4: propose ──

def test_propose_low_auto_returns_executable_packet(tmp_path):
    chan, led = _chan(tmp_path, risk="low", policy="auto")
    out = chan.propose(_intent())
    assert out["status"] == "executable"
    assert out["packet"] == {"tool": "deploy", "args": {"env": "prod"},
                             "ledger_id": out["ledger_id"]}
    assert led.get(out["ledger_id"])["status"] == "executing"
    led.close()


def test_propose_high_risk_is_pending(tmp_path):
    chan, led = _chan(tmp_path, risk="high", policy="auto")  # auto+high -> pending
    out = chan.propose(_intent())
    assert out["status"] == "pending_approval"
    assert led.get(out["ledger_id"])["status"] == "proposed"
    led.close()


def test_propose_require_approval_is_pending(tmp_path):
    chan, led = _chan(tmp_path, risk="low", policy="require_approval")
    assert chan.propose(_intent())["status"] == "pending_approval"
    led.close()


def test_propose_skeptic_fail_is_rejected(tmp_path):
    chan, led = _chan(tmp_path, verdict="FAIL")
    out = chan.propose(_intent())
    assert out["status"] == "rejected" and out["verdict"] == "FAIL"
    assert led.get(out["ledger_id"])["status"] == "failed"
    led.close()


def test_propose_unknown_tool_rejected(tmp_path):
    chan, led = _chan(tmp_path)
    out = chan.propose(_intent(tool="ghost"))
    assert out["status"] == "rejected" and "unknown" in out["reasons"][0].lower()
    led.close()


def test_propose_bad_args_rejected(tmp_path):
    chan, led = _chan(tmp_path)
    out = chan.propose({"tool": "deploy", "args": {}, "rationale": "x",
                        "expected_outcome": "y"})        # missing required 'env'
    assert out["status"] == "rejected"
    led.close()


def test_propose_when_asleep_is_gated(tmp_path):
    chan, led = _chan(tmp_path, awake=False)
    assert chan.propose(_intent()) == {"status": "gated",
                                       "reason": "engine not awake"}
    led.close()


def test_propose_when_lockdown_is_gated(tmp_path):
    chan, led = _chan(tmp_path, lockdown=True)
    assert chan.propose(_intent())["status"] == "gated"
    led.close()
