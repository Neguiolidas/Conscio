from conscio.agency.contracts import normalize_tool_name, proposal_from_dict
from conscio.agency.gateway import (
    _JSON_INSTRUCTIONS,
    _KV_INSTRUCTIONS,
    json_instructions,
    kv_instructions,
)


def test_normalize_tool_name_variations():
    # Trailing empty parentheses stripped
    assert normalize_tool_name("host_health()") == "host_health"

    # Wrapping backticks and quotes stripped
    assert normalize_tool_name("`host_health`") == "host_health"
    assert normalize_tool_name('"host_health"') == "host_health"
    assert normalize_tool_name("'host_health'") == "host_health"
    assert normalize_tool_name("``host_health``") == "host_health"
    assert normalize_tool_name('""host_health""') == "host_health"

    # Combination of backticks and parentheses
    assert normalize_tool_name("`host_health()`") == "host_health"
    assert normalize_tool_name('"host_health()"') == "host_health"

    # Leading/trailing whitespace
    assert normalize_tool_name("  host_health()  ") == "host_health"
    assert normalize_tool_name("  `host_health`  ") == "host_health"

    # Preserved unchanged: arguments inside parentheses, placeholders, other formats
    assert normalize_tool_name("host_health(x=1)") == "host_health(x=1)"
    assert normalize_tool_name("<tool name>") == "<tool name>"
    assert normalize_tool_name("Host_Health") == "Host_Health"  # No lowercasing!


def test_proposal_from_dict_applies_normalization():
    data = {
        "tool": "host_health()",
        "args": {"verbose": True},
        "rationale": "checking system health",
        "expected_outcome": "status report",
    }
    proposal = proposal_from_dict(data, goal_id="g1")
    assert proposal.tool == "host_health"


def test_instructions_do_not_contain_placeholder_and_include_tool_names():
    names = ["host_health", "check_disk", "read_logs"]

    # JSON instructions with tool_names
    j_text = json_instructions(names)
    assert "<tool name>" not in j_text
    for n in names:
        assert n in j_text

    # KV instructions with tool_names
    kv_text = kv_instructions(names)
    assert "<tool name>" not in kv_text
    for n in names:
        assert n in kv_text

    # Constants / instructions without tool_names also never contain '<tool name>'
    assert "<tool name>" not in _JSON_INSTRUCTIONS
    assert "<tool name>" not in _KV_INSTRUCTIONS
    assert "<tool name>" not in json_instructions()
    assert "<tool name>" not in kv_instructions()
