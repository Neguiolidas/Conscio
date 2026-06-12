# tests/test_agency_contracts.py
"""Tests for conscio.agency.contracts — dataclass contracts + zero-dep validator."""

from conscio.agency.contracts import (
    PROPOSAL_SCHEMA,
    VERDICT_SCHEMA,
    ActionProposal,
    AuditVerdict,
    ToolResult,
    proposal_from_dict,
    validate,
    verdict_from_dict,
)


class TestValidate:
    def test_valid_payload_returns_no_errors(self):
        data = {"tool": "fs_read", "args": {"path": "a.md"},
                "rationale": "check notes", "expected_outcome": "file content"}
        assert validate(data, PROPOSAL_SCHEMA) == []

    def test_missing_required_field_is_reported(self):
        errors = validate({"tool": "fs_read"}, PROPOSAL_SCHEMA)
        assert any("args" in e for e in errors)
        assert any("rationale" in e for e in errors)

    def test_wrong_type_is_reported(self):
        data = {"tool": 7, "args": [], "rationale": "x", "expected_outcome": "y"}
        errors = validate(data, PROPOSAL_SCHEMA)
        assert any("tool" in e and "str" in e for e in errors)
        assert any("args" in e and "dict" in e for e in errors)

    def test_enum_constraint(self):
        schema = {"level": {"type": "str", "required": True, "enum": ["low", "high"]}}
        assert validate({"level": "low"}, schema) == []
        assert validate({"level": "mid"}, schema) != []

    def test_unknown_keys_are_ignored(self):
        data = {"tool": "t", "args": {}, "rationale": "r",
                "expected_outcome": "e", "extra": 1}
        assert validate(data, PROPOSAL_SCHEMA) == []

    def test_non_dict_input_is_one_error(self):
        assert len(validate("not a dict", PROPOSAL_SCHEMA)) == 1


class TestProposalFromDict:
    def test_builds_dataclass_and_fills_ids(self):
        data = {"tool": "fs_read", "args": {"path": "a"}, "rationale": "r",
                "expected_outcome": "e"}
        p = proposal_from_dict(data, goal_id="abc123")
        assert isinstance(p, ActionProposal)
        assert p.tool == "fs_read" and p.goal_id == "abc123"
        assert len(p.action_id) == 32  # uuid4 hex

    def test_tool_result_defaults(self):
        r = ToolResult(ok=True, output="done")
        assert r.error == "" and r.duration_ms == 0


# ── F2: AuditVerdict ────────────────────────────────────────────────────


def test_audit_verdict_pass_property():
    assert AuditVerdict(verdict="PASS").passed is True
    assert AuditVerdict(verdict="FAIL").passed is False


def test_verdict_schema_validates_enum():
    assert validate({"verdict": "PASS"}, VERDICT_SCHEMA) == []
    errors = validate({"verdict": "MAYBE"}, VERDICT_SCHEMA)
    assert any("verdict" in e for e in errors)


def test_verdict_from_dict_defaults_and_clamp():
    v = verdict_from_dict({"verdict": "FAIL", "reasons": ["bad"],
                           "confidence": 7})
    assert v.verdict == "FAIL"
    assert v.reasons == ["bad"]
    assert v.risk_flags == []
    assert v.confidence == 1.0          # clamped to [0, 1]
    assert v.audited is True


def test_verdict_from_dict_non_numeric_confidence():
    v = verdict_from_dict({"verdict": "PASS", "confidence": "high"})
    assert v.confidence == 0.5
