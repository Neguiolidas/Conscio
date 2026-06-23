# tests/test_liaison_review.py
import pytest

from conscio.liaison import review


def test_fingerprint_deterministic():
    a = review.fingerprint("P", "g", "echo", {"x": 1}, 7)
    b = review.fingerprint("P", "g", "echo", {"x": 1}, 7)
    assert a == b and len(a) == 64


def test_distinct_proposers_distinct_fp():
    a = review.fingerprint("P1", "g", "echo", {"x": 1}, 7)
    b = review.fingerprint("P2", "g", "echo", {"x": 1}, 7)
    assert a != b                                     # Hermet anchor


def test_distinct_ledger_id_distinct_fp():
    a = review.fingerprint("P", "g", "echo", {"x": 1}, 7)
    b = review.fingerprint("P", "g", "echo", {"x": 1}, 8)
    assert a != b


def test_fingerprint_args_key_order_invariant():
    a = review.fingerprint("P", "g", "echo", {"x": 1, "y": 2}, 7)
    b = review.fingerprint("P", "g", "echo", {"y": 2, "x": 1}, 7)
    assert a == b                                     # canonical (sorted) JSON


def test_build_parse_request_roundtrip():
    p = review.build_request(fp="f", tool="echo", args={"x": 1}, goal="g",
                             verdict="PASS", rationale="r")
    rq = review.parse_request(p)
    assert (rq.fp, rq.tool, rq.args, rq.goal, rq.verdict, rq.rationale) == \
        ("f", "echo", {"x": 1}, "g", "PASS", "r")


def test_build_parse_verdict_roundtrip():
    p = review.build_verdict(fp="f", decision="approve", reason="ok")
    v = review.parse_verdict(p)
    assert (v.fp, v.decision, v.reason) == ("f", "approve", "ok")


def test_build_verdict_rejects_bad_decision():
    with pytest.raises(ValueError):
        review.build_verdict(fp="f", decision="maybe", reason="")


def test_parse_verdict_rejects_bad_decision():
    with pytest.raises(ValueError):
        review.parse_verdict({"fp": "f", "decision": "maybe"})


def test_parse_request_rejects_malformed():
    with pytest.raises(ValueError):
        review.parse_request({"tool": "echo"})        # no fp
    with pytest.raises(ValueError):
        review.parse_request("not a dict")
