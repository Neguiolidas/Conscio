# tests/test_agency_promote.py
import json

from conscio.agency.promote import (MIN_PROMOTE_PASSES, PromoteDecision,
                                     evaluate_promotion)


class _Reg:
    """Minimal registry stub: a tool exists iff its name is in `known`."""
    def __init__(self, known):
        self.known = set(known)

    def get(self, name):
        return object() if name in self.known else None


def _seq(*tools):
    return json.dumps(list(tools))


def test_enough_clean_passes_ok():
    d = evaluate_promotion(trial_successes=3, trial_failures=0,
                           tool_seq=_seq("fs_read"), registry=_Reg(["fs_read"]))
    assert d == PromoteDecision(True, "")


def test_boundary_three_passes_ok():
    assert MIN_PROMOTE_PASSES == 3
    d = evaluate_promotion(trial_successes=3, trial_failures=0,
                           tool_seq=_seq("fs_read"), registry=_Reg(["fs_read"]))
    assert d.ok


def test_insufficient_passes_refused():
    d = evaluate_promotion(trial_successes=2, trial_failures=0,
                           tool_seq=_seq("fs_read"), registry=_Reg(["fs_read"]))
    assert not d.ok and "insufficient" in d.reason


def test_any_failure_refused():
    d = evaluate_promotion(trial_successes=9, trial_failures=1,
                           tool_seq=_seq("fs_read"), registry=_Reg(["fs_read"]))
    assert not d.ok and "failed" in d.reason


def test_unknown_tool_refused():
    d = evaluate_promotion(trial_successes=5, trial_failures=0,
                           tool_seq=_seq("fs_read", "rm_rf"),
                           registry=_Reg(["fs_read"]))
    assert not d.ok and "rm_rf" in d.reason


def test_corrupt_tool_seq_refused():
    d = evaluate_promotion(trial_successes=5, trial_failures=0,
                           tool_seq='{"not":"a list"}', registry=_Reg([]))
    assert not d.ok and "corrupt tool_seq" in d.reason
