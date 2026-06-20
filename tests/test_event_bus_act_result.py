# tests/test_event_bus_act_result.py
from conscio.event_bus import EventBus


def test_event_bus_accepts_act_result(tmp_path):
    bus = EventBus(db_path=tmp_path / "c.db")
    try:
        bus.emit(type="act:result", category="external",
                 data={"tool": "deploy", "ledger_id": 1, "ok": True})
        rows = bus.query(type="act:result", limit=5)
        assert len(rows) == 1
    finally:
        bus.close()
