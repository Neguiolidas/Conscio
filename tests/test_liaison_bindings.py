# tests/test_liaison_bindings.py
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
        seen.close(); eng.close()


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
        seen.close(); eng.close()


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
        seen.close(); eng.close()


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
        seen.close(); eng.close()


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
        seen.close(); eng.close()


def test_review_approve_unknown_fp(tmp_path):
    b, eng, seen = _bind(tmp_path, instance_id="HERMES")
    try:
        assert b._review_approve({"fp": "ghost"}) == {"ok": False,
                                                      "reason": "unknown_fp"}
    finally:
        seen.close(); eng.close()
