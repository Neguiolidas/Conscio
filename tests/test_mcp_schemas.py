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
    assert {"conscio_feed", "conscio_note", "conscio_advisory",
            "conscio_recall", "conscio_propose_action",
            "conscio_propose_plan"} <= names
    assert "conscio_act" not in names          # deferred to v2.0.1
    for d in s.BASE_TOOL_DEFS:
        assert "inputSchema" in d


def _every_tool_def():
    """Every tool def the module exports, discovered rather than listed.

    Discovery, not a hand-written list: a future ``FOO_TOOL_DEFS`` is covered
    the day it is added, which is exactly when a bad name would slip in.
    """
    out = []
    for attr in dir(s):
        if not attr.endswith(("TOOL_DEF", "TOOL_DEFS")):
            continue
        val = getattr(s, attr)
        out += [val] if isinstance(val, dict) else list(val)
    return out


def test_no_tool_name_breaks_the_host_name_rule():
    """A dot is legal in MCP and still gets the whole surface disabled.

    v4.1: Verdent applies the Anthropic/OpenAI function-name rule to MCP tools
    and dropped every one of ours because they were ``conscio.x`` — its log read
    ``loaded_tools: 0, unloaded_tools: 18``, the whole ``balanced`` surface. The
    protocol does not forbid the dot (``Tool.name`` is an unconstrained string in
    every schema revision), so nothing here failed — the host just served zero
    tools. This pins the intersection of what the spec allows and what hosts
    actually accept, over every name the server can serve rather than the 18
    that happened to be served when it was caught.
    """
    import re

    defs = _every_tool_def()
    assert len(defs) >= 48, f"discovery found only {len(defs)} defs; it broke"
    rule = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
    bad = [d["name"] for d in defs if not rule.match(d["name"])]
    assert not bad, f"tool names rejected by strict hosts: {bad}"


def test_propose_plan_requires_goal_and_tools():
    pp = next(d for d in s.BASE_TOOL_DEFS if d["name"] == "conscio_propose_plan")
    assert set(pp["inputSchema"]["required"]) == {"goal", "tools"}
