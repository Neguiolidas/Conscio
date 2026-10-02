from conscio.agency.contracts import normalize_tool_name, proposal_from_dict
from conscio.agency.gateway import (
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
    # Pass unsorted names to verify sorted output
    names = ["read_logs", "check_disk", "host_health"]
    sorted_expected = "check_disk, host_health, read_logs"

    # JSON instructions with tool_names
    j_text = json_instructions(names)
    assert "<tool name>" not in j_text
    # No value slot for tool containing prose (e.g. '{"tool": "' or '"tool": "')
    assert '"tool": "' not in j_text
    # Tool names appear on the key description line in sorted order
    j_tool_lines = [line for line in j_text.splitlines() if '"tool":' in line]
    assert len(j_tool_lines) == 1
    assert sorted_expected in j_tool_lines[0]

    # KV instructions with tool_names
    kv_text = kv_instructions(names)
    assert "<tool name>" not in kv_text
    # The line describing TOOL is separate from the format block
    assert "TOOL must be one of: " + sorted_expected in kv_text
    # In format block, TOOL does not contain instruction prose
    kv_format_lines = [line for line in kv_text.splitlines() if line.startswith("TOOL:")]
    assert len(kv_format_lines) == 1
    assert kv_format_lines[0] == "TOOL: <name>"

    # Instructions without tool_names
    j_no_names = json_instructions()
    assert "<tool name>" not in j_no_names
    assert '"tool": "' not in j_no_names
    assert '"tool": exact name' in j_no_names

    kv_no_names = kv_instructions()
    assert "<tool name>" not in kv_no_names
    assert "TOOL must be the exact name" in kv_no_names
