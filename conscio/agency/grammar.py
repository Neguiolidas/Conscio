# conscio/agency/grammar.py
"""
Embedded schema -> GBNF compiler (spec section 5.3, tier 1).

Compiles an output contract plus the registry's tool names into a
llama.cpp GBNF grammar: structurally valid JSON, the right keys in
order, and `tool` constrained to an alternation of registered names.
Fine-grained validation of args stays in contracts.validate — the
grammar constrains shape, not semantics. Zero-dep.
"""
from __future__ import annotations

import json

# Standard JSON rules (llama.cpp GBNF dialect).
_JSON_BASE = r"""
value   ::= object | array | string | number | boolean | null
object  ::= "{" ws ( string ws ":" ws value ( "," ws string ws ":" ws value )* )? ws "}"
array   ::= "[" ws ( value ( "," ws value )* )? ws "]"
string  ::= "\"" char* "\""
char    ::= [^"\\\x00-\x1f] | "\\" ( ["\\/bfnrt] | "u" hex hex hex hex )
hex     ::= [0-9a-fA-F]
number  ::= "-"? ( "0" | [1-9] [0-9]* ) ( "." [0-9]+ )? ( [eE] [-+]? [0-9]+ )?
boolean ::= "true" | "false"
null    ::= "null"
ws      ::= [ \t\n\r]*
"""

_TYPE_RULES = {"str": "string", "int": "number", "float": "number",
               "bool": "boolean", "dict": "object", "list": "array"}


def _literal(value: str) -> str:
    """GBNF terminal matching the JSON string encoding of `value`.

    Two escape levels: the value is JSON-encoded first (so the model must
    emit a valid JSON string), then GBNF-escaped to form the terminal.
    """
    encoded = json.dumps(value)
    inner = encoded.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{inner}"'


def compile_schema_grammar(schema: dict[str, dict], *,
                           enums: dict[str, list[str]] | None = None) -> str:
    """Compile a contracts-style schema dict into a GBNF grammar.

    `enums` overrides per-field allowed string values (used for `tool`);
    a field's own schema-level "enum" is honored when no override exists.
    """
    enums = enums or {}
    parts: list[str] = []
    rules: list[str] = []
    for index, (key, spec) in enumerate(schema.items()):
        rule = f"f{index}"
        allowed = enums.get(key) or spec.get("enum")
        if allowed:
            rules.append(f"{rule} ::= " + " | ".join(
                _literal(str(v)) for v in allowed))
        else:
            rules.append(f"{rule} ::= "
                         + _TYPE_RULES.get(spec.get("type", "str"), "string"))
        parts.append(f'{_literal(key)} ws ":" ws {rule}')
    body = ' "," ws '.join(parts)
    root = f'root ::= ws "{{" ws {body} ws "}}" ws'
    return "\n".join([root, *rules]) + _JSON_BASE


def compile_proposal_grammar(tool_names: list[str]) -> str:
    """ActionProposal grammar with `tool` locked to the registry."""
    from .contracts import PROPOSAL_SCHEMA
    enums = {"tool": sorted(tool_names)} if tool_names else {}
    return compile_schema_grammar(PROPOSAL_SCHEMA, enums=enums)
