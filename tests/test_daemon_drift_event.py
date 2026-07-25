"""Daemon emits structure:changed event when drift detected (v3.4 B1)."""
from conscio.event_bus import EventBus


def test_structure_changed_is_valid_type():
    bus = EventBus(":memory:")
    bus.emit(type="structure:changed", category="system",
             data={"workspace_id": "ws-1", "graph_commit": "abc", "head_commit": "def"})
    events = bus.query(type="structure:changed")
    assert len(events) == 1
    assert events[0].data["workspace_id"] == "ws-1"
