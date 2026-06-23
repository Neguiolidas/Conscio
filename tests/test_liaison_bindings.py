# tests/test_liaison_bindings.py
from types import SimpleNamespace

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine
from conscio.liaison import mailbox, review
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


def _bind(tmp_path, *, instance_id, reviewers=(), hermes_review=True,
          act_flag=False, liaison_db=None, storage=None):
    eng = ConsciousnessEngine("glm-5.1", storage_path=storage or tmp_path)
    seen = SeenStore((storage or tmp_path) / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name=None, workspace_id="ws",
                 act_flag=act_flag, hermes_review=hermes_review,
                 reviewers=tuple(reviewers), self_instance_id=instance_id,
                 liaison_db=liaison_db or (tmp_path / "liaison.db"))
    return b, eng, seen


def test_liaison_tools_absent_without_flag(tmp_path):
    b, eng, seen = _bind(tmp_path, instance_id="X", hermes_review=False)
    try:
        names = {t["name"] for t in b.tool_defs()}
        assert not any(n.startswith("conscio.review") for n in names)
        assert "conscio.reviews" not in names and "conscio.poll_reviews" not in names
    finally:
        seen.close()
        eng.close()


def test_reviewer_tools_present_without_act(tmp_path):
    b, eng, seen = _bind(tmp_path, instance_id="X", hermes_review=True,
                         act_flag=False)
    try:
        names = {t["name"] for t in b.tool_defs()}
        assert {"conscio.reviews", "conscio.review_approve",
                "conscio.review_reject"} <= names
        assert "conscio.poll_reviews" not in names      # needs --enable-act
        meta = b.conscio_meta()
        assert meta["hermes_review_enabled"] is True
    finally:
        seen.close()
        eng.close()


def test_reviews_lists_and_dedups_by_fp(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _bind(tmp_path, instance_id="HERMES", liaison_db=db)
    try:
        # two request rows, same fp (a duplicate) + one distinct fp
        for _ in range(2):
            mailbox.send(db, from_instance="CLAUDE", to_instance="HERMES",
                         type="review_request",
                         payload=review.build_request(
                             fp="fp1", tool="echo", args={"x": 1}, goal="g",
                             verdict="PASS", rationale="r"))
        mailbox.send(db, from_instance="CLAUDE", to_instance="HERMES",
                     type="review_request",
                     payload=review.build_request(
                         fp="fp2", tool="rm", args={}, goal="g2",
                         verdict="PASS", rationale="r2"))
        rows = b._reviews({})
        assert sorted(r["fp"] for r in rows) == ["fp1", "fp2"]   # deduped
        assert {r["from_instance"] for r in rows} == {"CLAUDE"}
    finally:
        seen.close()
        eng.close()


def test_review_approve_emits_verdict_and_marks_request_read(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _bind(tmp_path, instance_id="HERMES", liaison_db=db)
    try:
        mailbox.send(db, from_instance="CLAUDE", to_instance="HERMES",
                     type="review_request",
                     payload=review.build_request(
                         fp="fp1", tool="echo", args={}, goal="g",
                         verdict="PASS", rationale="r"))
        res = b._review_approve({"fp": "fp1"})
        assert res["ok"] is True and res["to"] == "CLAUDE"
        # verdict landed in CLAUDE's inbox
        v = mailbox.inbox(db, "CLAUDE", types=["review_verdict"])
        assert v and v[0]["payload"]["decision"] == "approve"
        # request marked read on HERMES side
        assert b._reviews({}) == []
    finally:
        seen.close()
        eng.close()


def test_review_reject_emits_reject_verdict(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _bind(tmp_path, instance_id="HERMES", liaison_db=db)
    try:
        mailbox.send(db, from_instance="CLAUDE", to_instance="HERMES",
                     type="review_request",
                     payload=review.build_request(
                         fp="fpz", tool="rm", args={}, goal="g",
                         verdict="PASS", rationale="r"))
        res = b._review_reject({"fp": "fpz", "reason": "too risky"})
        assert res["ok"] is True
        v = mailbox.inbox(db, "CLAUDE", types=["review_verdict"])
        assert v[0]["payload"] == {"fp": "fpz", "decision": "reject",
                                   "reason": "too risky"}
    finally:
        seen.close()
        eng.close()


def test_review_approve_unknown_fp(tmp_path):
    b, eng, seen = _bind(tmp_path, instance_id="HERMES")
    try:
        assert b._review_approve({"fp": "ghost"}) == {"ok": False,
                                                      "reason": "unknown_fp"}
    finally:
        seen.close()
        eng.close()


# ── proposer side (auto-publish + poll) ──

class _PassSkeptic:
    """Force the host-act audit to PASS so propose() reaches pending — the
    liaison wiring under test is independent of the real Skeptic/adapter."""
    def audit(self, proposal, goal_text=""):
        return SimpleNamespace(passed=True, verdict="PASS", reasons=[],
                               risk_flags=[], confidence=0.9)


_HR_MANIFEST = [{"name": "echo",
                 "params": {"msg": {"type": "str", "required": True}},
                 "risk": "low", "approval_policy": "hermes_review",
                 "description": "echo"}]
_INTENT = {"tool": "echo", "args": {"msg": "hi"}, "rationale": "r",
           "expected_outcome": "ok", "goal": "g"}


def _proposer(tmp_path, storage, *, instance_id, reviewers, liaison_db):
    eng = ConsciousnessEngine("glm-5.1", storage_path=storage)
    eng.attach_adapter(MockAdapter(script=[]))
    eng.wake()                                       # _gate() needs awake
    assert eng.enable_host_act(_HR_MANIFEST) is True
    eng.host_act.skeptic = _PassSkeptic()            # decouple from real skeptic
    seen = SeenStore(storage / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name="mock", workspace_id="ws",
                 act_flag=True, hermes_review=True, reviewers=tuple(reviewers),
                 self_instance_id=instance_id, liaison_db=liaison_db)
    return b, eng, seen


def test_propose_hermes_review_publishes_one_request_per_reviewer(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                             reviewers=("B", "C"), liaison_db=db)
    try:
        res = b._act({"intent": _INTENT})
        assert res["status"] == "pending_approval"
        assert len(mailbox.inbox(db, "B", types=["review_request"])) == 1
        assert len(mailbox.inbox(db, "C", types=["review_request"])) == 1
    finally:
        seen.close()
        eng.close()


def test_flag_off_no_publish(tmp_path):
    db = tmp_path / "liaison.db"
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "A")
    eng.attach_adapter(MockAdapter(script=[]))
    eng.wake()
    eng.enable_host_act(_HR_MANIFEST)
    eng.host_act.skeptic = _PassSkeptic()
    seen = SeenStore(tmp_path / "A" / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name="mock", workspace_id="ws",
                 act_flag=True, hermes_review=False, reviewers=("B",),
                 self_instance_id="A", liaison_db=db)
    try:
        b._act({"intent": _INTENT})
        assert mailbox.inbox(db, "B") == []           # nothing published
    finally:
        seen.close()
        eng.close()


def test_poll_applies_approve_and_returns_packet(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                             reviewers=("B",), liaison_db=db)
    try:
        b._act({"intent": _INTENT})
        req = mailbox.inbox(db, "B", types=["review_request"])[0]
        fp = req["payload"]["fp"]
        mailbox.send(db, from_instance="B", to_instance="A",
                     type="review_verdict",
                     payload=review.build_verdict(fp=fp, decision="approve",
                                                  reason=""))
        applied = b._poll_reviews({})
        assert len(applied) == 1
        assert applied[0]["decision"] == "approve"
        assert applied[0]["status"] == "executable"
        assert applied[0]["packet"]["tool"] == "echo"
    finally:
        seen.close()
        eng.close()


def test_poll_applies_reject(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                             reviewers=("B",), liaison_db=db)
    try:
        b._act({"intent": _INTENT})
        fp = mailbox.inbox(db, "B", types=["review_request"])[0]["payload"]["fp"]
        mailbox.send(db, from_instance="B", to_instance="A",
                     type="review_verdict",
                     payload=review.build_verdict(fp=fp, decision="reject",
                                                  reason="no"))
        applied = b._poll_reviews({})
        assert applied[0]["decision"] == "reject"
        assert applied[0]["status"] == "rejected"
    finally:
        seen.close()
        eng.close()


def test_poll_ignores_non_allowlisted_verdict(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                             reviewers=("B",), liaison_db=db)
    try:
        b._act({"intent": _INTENT})
        fp = mailbox.inbox(db, "B", types=["review_request"])[0]["payload"]["fp"]
        # a stranger (not in reviewers) tries to approve
        mailbox.send(db, from_instance="EVIL", to_instance="A",
                     type="review_verdict",
                     payload=review.build_verdict(fp=fp, decision="approve",
                                                  reason=""))
        assert b._poll_reviews({}) == []              # ignored
        # the act is still pending (not released)
        assert b.engine.host_act.pending(10)[0]["status"] == "proposed"
    finally:
        seen.close()
        eng.close()


def test_poll_ignores_foreign_proposer_fp(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                             reviewers=("B",), liaison_db=db)
    try:
        b._act({"intent": _INTENT})
        # allowlisted reviewer, but fp computed for a DIFFERENT proposer
        bogus = review.fingerprint("OTHER", "g", "echo", {"msg": "hi"}, 1)
        mailbox.send(db, from_instance="B", to_instance="A",
                     type="review_verdict",
                     payload=review.build_verdict(fp=bogus, decision="approve",
                                                  reason=""))
        assert b._poll_reviews({}) == []              # no local match
    finally:
        seen.close()
        eng.close()


def test_poll_replay_is_noop(tmp_path):
    db = tmp_path / "liaison.db"
    b, eng, seen = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                             reviewers=("B",), liaison_db=db)
    try:
        b._act({"intent": _INTENT})
        fp = mailbox.inbox(db, "B", types=["review_request"])[0]["payload"]["fp"]
        mailbox.send(db, from_instance="B", to_instance="A",
                     type="review_verdict",
                     payload=review.build_verdict(fp=fp, decision="approve",
                                                  reason=""))
        assert len(b._poll_reviews({})) == 1          # first apply
        # replay the same verdict (re-send a fresh row, same fp)
        mailbox.send(db, from_instance="B", to_instance="A",
                     type="review_verdict",
                     payload=review.build_verdict(fp=fp, decision="approve",
                                                  reason=""))
        assert b._poll_reviews({}) == []              # row no longer 'proposed'
    finally:
        seen.close()
        eng.close()


def test_two_instance_end_to_end(tmp_path):
    db = tmp_path / "liaison.db"
    A, engA, seenA = _proposer(tmp_path, tmp_path / "A", instance_id="A",
                               reviewers=("B",), liaison_db=db)
    B, engB, seenB = _bind(tmp_path, instance_id="B", hermes_review=True,
                           liaison_db=db, storage=tmp_path / "B")
    try:
        A._act({"intent": _INTENT})                   # A proposes
        reqs = B._reviews({})                          # B sees it
        assert len(reqs) == 1
        B._review_approve({"fp": reqs[0]["fp"]})       # B approves
        applied = A._poll_reviews({})                  # A applies
        assert applied[0]["status"] == "executable"
    finally:
        seenA.close()
        engA.close()
        seenB.close()
        engB.close()


def test_argparser_accepts_liaison_flags():
    from conscio.mcp.server import _arg_parser
    ns = _arg_parser().parse_args(
        ["--enable-hermes-review", "--reviewer", "B", "--reviewer", "C",
         "--liaison-db", "/tmp/x.db"])
    assert ns.enable_hermes_review is True
    assert ns.reviewer == ["B", "C"]
    assert ns.liaison_db == "/tmp/x.db"
