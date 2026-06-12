# tests/test_agency_grammar.py
"""Embedded schema->GBNF compiler (tier 1, spec section 5.3)."""
from conscio.agency.contracts import PROPOSAL_SCHEMA
from conscio.agency.grammar import (compile_proposal_grammar,
                                    compile_schema_grammar)


class TestCompileSchema:
    def test_root_rule_lists_fields_in_order(self):
        g = compile_schema_grammar(PROPOSAL_SCHEMA)
        root = next(l for l in g.splitlines() if l.startswith("root ::="))
        assert root.index('tool') < root.index('args')
        assert root.index('args') < root.index('rationale')
        assert root.index('rationale') < root.index('expected_outcome')

    def test_types_map_to_json_rules(self):
        g = compile_schema_grammar({"a": {"type": "str"},
                                    "b": {"type": "dict"},
                                    "c": {"type": "int"},
                                    "d": {"type": "list"},
                                    "e": {"type": "bool"}})
        assert "f0 ::= string" in g
        assert "f1 ::= object" in g
        assert "f2 ::= number" in g
        assert "f3 ::= array" in g
        assert "f4 ::= boolean" in g

    def test_enum_override_builds_alternation(self):
        g = compile_schema_grammar({"tool": {"type": "str"}},
                                   enums={"tool": ["fs_read", "fs_write"]})
        assert 'f0 ::= "\\"fs_read\\"" | "\\"fs_write\\""' in g

    def test_schema_level_enum_used(self):
        g = compile_schema_grammar(
            {"color": {"type": "str", "enum": ["red", "blue"]}})
        assert '"\\"red\\""' in g and '"\\"blue\\""' in g

    def test_base_json_rules_present(self):
        g = compile_schema_grammar(PROPOSAL_SCHEMA)
        for rule in ("value", "object", "array", "string",
                     "number", "boolean", "null", "ws"):
            assert f"{rule}" in g and "::=" in g
        for line_start in ("value ", "object ", "array ", "string ",
                           "number ", "boolean ", "null ", "ws "):
            assert any(line.startswith(line_start)
                       for line in g.splitlines())

    def test_literal_escapes_quotes_and_backslashes(self):
        g = compile_schema_grammar({"t": {"type": "str"}},
                                   enums={"t": ['a"b\\c']})
        assert '\\\\\\"' in g          # escaped quote inside GBNF literal


class TestProposalGrammar:
    def test_tool_alternation_from_registry_names(self):
        g = compile_proposal_grammar(["fs_write", "fs_read"])
        assert '"\\"fs_read\\"" | "\\"fs_write\\""' in g   # sorted

    def test_empty_tool_names_falls_back_to_string(self):
        g = compile_proposal_grammar([])
        assert "f0 ::= string" in g
