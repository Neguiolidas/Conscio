# tests/test_mcp_schemas.py
from conscio.mcp import schemas as s


def test_valid_event_passes():
    ev = {"id": "e1", "type": "perception", "source": "host",
          "category": "host", "payload": {"cpu": 0.4, "msg": "ok"}}
    assert s.validate_event(ev) == []


def test_missing_required_field_fails():
    errs = s.validate_event({"type": "perception", "source": "host", "payload": {}})
    assert any("category" in e for e in errs)


def test_event_to_frame_splits_numeric_and_text():
    ev = {"type": "perception", "source": "host", "category": "host",
          "ts": 1750000000.0, "payload": {"cpu": 0.4, "note": "spike", "up": True}}
    frame = s.event_to_frame(ev)
    assert frame.source == "host:host"
    assert frame.signals == {"cpu": 0.4}
    assert "note: spike" in frame.observations
    assert "up=True" in frame.observations
    assert frame.ts == 1750000000.0


def test_derive_event_id_uses_explicit_id():
    assert s.derive_event_id({"id": "abc"}) == "abc"


def test_derive_event_id_deterministic_when_absent():
    ev = {"type": "x", "source": "y", "category": "z", "payload": {"a": 1}}
    assert s.derive_event_id(ev) == s.derive_event_id(dict(ev))


def test_base_tool_defs_have_name_and_input_schema():
    names = {d["name"] for d in s.BASE_TOOL_DEFS}
    assert {"conscio.feed", "conscio.note", "conscio.advisory",
            "conscio.recall", "conscio.propose_action",
            "conscio.propose_plan"} <= names
    assert "conscio.act" not in names          # deferred to v2.0.1
    for d in s.BASE_TOOL_DEFS:
        assert "inputSchema" in d


def test_propose_plan_requires_goal_and_tools():
    pp = next(d for d in s.BASE_TOOL_DEFS if d["name"] == "conscio.propose_plan")
    assert set(pp["inputSchema"]["required"]) == {"goal", "tools"}
