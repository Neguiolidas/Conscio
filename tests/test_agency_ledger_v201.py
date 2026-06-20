# tests/test_agency_ledger_v201.py
from conscio.agency.ledger import ActionLedger


def _led(tmp_path):
    return ActionLedger(tmp_path / "conscio.db")


def test_record_persists_approval_policy(tmp_path):
    led = _led(tmp_path)
    rid = led.record(goal_fp="g", tool="deploy", args_json="{}", rationale="",
                     tier="host", status="proposed",
                     approval_policy="hermes_review")
    assert led.get(rid)["approval_policy"] == "hermes_review"
    led.close()


def test_has_in_flight_true_for_proposed_and_executing(tmp_path):
    led = _led(tmp_path)
    assert led.has_in_flight() is False
    rid = led.record(goal_fp="g", tool="t", args_json="{}", rationale="",
                     tier="host", status="proposed")
    assert led.has_in_flight() is True
    led.update_execution(rid, ok=True, output="", error="", duration_ms=0,
                         status="executed")
    assert led.has_in_flight() is False
    led.close()


def test_reopen_keeps_approval_policy(tmp_path):
    led = _led(tmp_path)
    led.close()
    led2 = ActionLedger(tmp_path / "conscio.db")        # re-open, ALTER guard
    rid = led2.record(goal_fp="g", tool="t", args_json="{}", rationale="",
                      tier="host", status="proposed", approval_policy="auto")
    assert led2.get(rid)["approval_policy"] == "auto"
    led2.close()
