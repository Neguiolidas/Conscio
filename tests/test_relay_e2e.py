# tests/test_relay_e2e.py
"""v2.6.3 #5: end-to-end integration of the v2.6.2 Awake Relay loop wired
against REAL parts simultaneously — real liaison mailbox, real HostActChannel +
ledger, the daemon-side RelaySensor (perception, read-only), and the
server-side Bindings auto-apply (--auto-review). Only the Skeptic/Breaker/Trust
and the engine shell are faked; the perceive -> verdict -> approve path is real.
Converts the previously-manual "full loop" claim into a regression guard."""
import json

from conscio.agency.contracts import AuditVerdict
from conscio.agency.host_act import HostActChannel
from conscio.agency.ledger import ActionLedger
from conscio.agency.tools import registry_from_manifest
from conscio.liaison import mailbox, review
from conscio.mcp.server import Bindings
from conscio.perception.relay_sensor import RelaySensor

SELF = "me-instance-0001"
REVIEWER = "hermet-instance-0002"


class _PassSkeptic:
    def audit(self, proposal, *, goal_text=""):
        return AuditVerdict(verdict="PASS", reasons=[], risk_flags=[],
                            confidence=0.9)


class _NoBreaker:
    def global_lockdown_due(self): return False
    def should_trip(self, goal_fp, *, task_type=""): return False
    def trip(self, goal_fp, *, detail="", goal_text=""): pass


class _Trust:
    def on_success(self, tool): pass


class _Seen:
    pass


class _Engine:
    """Minimal engine carrier: real host_act behind the surface Bindings reads."""
    def __init__(self, host_act):
        self.awake = True
        self.host_act = host_act

    def advisory(self):
        return {"ok": True}


def _host_act(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    manifest = [{"name": "deploy",
                 "params": {"env": {"type": "str", "required": True}},
                 "risk": "low", "approval_policy": "require_approval"}]
    chan = HostActChannel(
        ledger=led, skeptic=_PassSkeptic(), breaker=_NoBreaker(), trust=_Trust(),
        registry=registry_from_manifest(manifest),
        emit_fn=lambda **kw: None, awake_fn=lambda: True)
    return chan, led


def _bindings(engine, db):
    return Bindings(engine, _Seen(), auto_review=True, hermes_review=True,
                    reviewers=(REVIEWER,), self_instance_id=SELF, liaison_db=db)


def test_full_loop_perceive_then_auto_apply(tmp_path):
    db = tmp_path / "liaison.db"
    chan, led = _host_act(tmp_path)

    # 1. propose a require_approval act -> parks pending in the real ledger.
    out = chan.propose({"tool": "deploy", "args": {"env": "prod"},
                        "rationale": "ship", "expected_outcome": "live"})
    assert out["status"] == "pending_approval"
    rid = out["ledger_id"]
    assert led.get(rid)["status"] == "proposed"

    # 2. an allowlisted reviewer sends an approve verdict for THIS act's fp.
    row = chan.pending(10)[0]
    fp = review.fingerprint(SELF, row["goal_fp"], row["tool"],
                            json.loads(row["args_json"]), row["id"])
    mailbox.send(db, from_instance=REVIEWER, to_instance=SELF,
                 type="review_verdict",
                 payload=review.build_verdict(fp=fp, decision="approve",
                                              reason=""))

    # 3. DAEMON side: RelaySensor perceives the pending verdict, read-only.
    frame = RelaySensor(db, SELF, [REVIEWER]).perceive()
    assert frame.signals["review_pending"] == 1.0
    assert mailbox.inbox(db, SELF, types=["review_verdict"],
                         unread_only=True)            # sensor did NOT consume

    # 4. SERVER side: a plain tool call (NOT poll_reviews) auto-applies it.
    eng = _Engine(chan)
    b = _bindings(eng, db)
    b.call_tool("conscio.advisory", {})

    # 5. the act is approved by the host_act gate, the verdict is consumed.
    assert led.get(rid)["status"] == "executing"
    assert mailbox.inbox(db, SELF, types=["review_verdict"],
                         unread_only=True) == []
    led.close()


def test_non_peer_verdict_does_not_apply(tmp_path):
    """A verdict from a non-allowlisted sender never moves the act."""
    db = tmp_path / "liaison.db"
    chan, led = _host_act(tmp_path)
    out = chan.propose({"tool": "deploy", "args": {"env": "prod"},
                        "rationale": "ship", "expected_outcome": "live"})
    rid = out["ledger_id"]
    row = chan.pending(10)[0]
    fp = review.fingerprint(SELF, row["goal_fp"], row["tool"],
                            json.loads(row["args_json"]), row["id"])
    mailbox.send(db, from_instance="stranger-9999", to_instance=SELF,
                 type="review_verdict",
                 payload=review.build_verdict(fp=fp, decision="approve",
                                              reason=""))
    b = _bindings(_Engine(chan), db)
    b.call_tool("conscio.advisory", {})
    assert led.get(rid)["status"] == "proposed"       # untouched
    led.close()
