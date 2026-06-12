# conscio/agency/gateway.py
"""
OutputGateway — turns raw cortex text into a valid ActionProposal
(spec section 5.3). F1 ships tier 2 (JSON mode + lenient repair + retry)
and tier 3 (KV-line for small models). F3 adds tier 1: GBNF constrained
decoding via the embedded grammar compiler, with `tool` locked to the
registry alternation. Tier comes from the measured ModelProfile when one
exists (explicit `tier`); otherwise from the adapter caps.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .adapter import AdapterError, InferenceAdapter
from .contracts import ActionProposal, proposal_from_dict, validate


class GatewayError(Exception):
    """All decode tiers failed for this cycle."""


# ── lenient JSON repair (vendored, ~40 lines) ──────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def repair_json(text: str) -> str:
    """Best-effort extraction of a JSON object from model output."""
    match = _FENCE_RE.search(text)
    if match:
        text = match.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text.strip()


# ── KV-line format (tier 3) ────────────────────────────────────────────

_KV_KEYS = {"TOOL": "tool", "WHY": "rationale", "EXPECT": "expected_outcome"}


def parse_kv(text: str) -> dict[str, Any]:
    """Parse the flat KV-line action format. Deterministic, no nesting."""
    data: dict[str, Any] = {"args": {}}
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("ARG "):
            body = line[4:]
            if "=" in body:
                name, _, value = body.partition("=")
                data["args"][name.strip()] = value.strip()
            continue
        key, _, value = line.partition(":")
        field = _KV_KEYS.get(key.strip().upper())
        if field:
            data[field] = value.strip()
    return data


def coerce(value: str, type_name: str) -> Any:
    """Coerce a KV string value using a tool's params schema type."""
    if type_name == "int":
        return int(value)
    if type_name == "float":
        return float(value)
    if type_name == "bool":
        return value.strip().lower() in ("true", "1", "yes")
    return value


_JSON_INSTRUCTIONS = (
    "\n\nRespond with ONE JSON object only, no prose, exactly these keys:\n"
    '{"tool": "<tool name>", "args": {<tool arguments>}, '
    '"rationale": "<why>", "expected_outcome": "<what should happen>"}')

_KV_INSTRUCTIONS = (
    "\n\nRespond with EXACTLY these lines and nothing else:\n"
    "TOOL: <tool name>\n"
    "ARG <name> = <value>   (one line per argument; omit if none)\n"
    "WHY: <one sentence>\n"
    "EXPECT: <one sentence>")


class OutputGateway:
    """Decode tier selection + retry loop. One gateway per adapter."""

    def __init__(self, adapter: InferenceAdapter, *, max_retries: int = 2,
                 tier: str | None = None):
        self.adapter = adapter
        self.max_retries = max_retries
        self.tier = tier         # explicit "T1"/"T2"/"T3"; None = caps auto
        self.last_tier = ""      # tier that produced (or last tried) decode

    def effective_tier(self) -> str:
        """Tier request_action will use: explicit, else adapter caps."""
        if self.tier is not None:
            return self.tier
        caps = self.adapter.capabilities()
        return "T1" if caps.grammar else "T2" if caps.json_mode else "T3"

    def request_action(self, base_prompt: str, schema: dict,
                       *, goal_id: str = "",
                       tool_names: list[str] | None = None) -> ActionProposal:
        caps = self.adapter.capabilities()
        tier = self.effective_tier()
        if tier == "T1":
            self.last_tier = "T1"
            data = self._try_grammar(base_prompt, schema, tool_names)
            if data is None:                   # single downgrade per cycle
                if caps.json_mode:
                    self.last_tier = "T2"
                    data = self._try_json(base_prompt, schema)
                else:
                    self.last_tier = "T3"
                    data = self._try_kv(base_prompt, schema, attempts=1)
        elif tier == "T2":
            self.last_tier = "T2"
            data = self._try_json(base_prompt, schema)
            if data is None:                   # single downgrade T2 -> T3
                self.last_tier = "T3"
                data = self._try_kv(base_prompt, schema, attempts=1)
        else:
            self.last_tier = "T3"
            data = self._try_kv(base_prompt, schema,
                                attempts=1 + self.max_retries)
        if data is None:
            raise GatewayError("all decode tiers failed")
        return proposal_from_dict(data, goal_id=goal_id)

    # ── tiers ──

    def _try_grammar(self, base_prompt: str, schema: dict,
                     tool_names: list[str] | None) -> dict | None:
        from .grammar import compile_schema_grammar
        enums = {"tool": sorted(tool_names)} if tool_names else {}
        grammar = compile_schema_grammar(schema, enums=enums)
        prompt = base_prompt + _JSON_INSTRUCTIONS
        feedback = ""
        for _ in range(1 + self.max_retries):
            try:
                raw = self.adapter.generate(prompt + feedback, schema=schema,
                                            grammar=grammar).text
            except AdapterError:
                return None
            try:
                data = json.loads(repair_json(raw))
            except (json.JSONDecodeError, ValueError):
                feedback = "\n\nPrevious answer was invalid JSON. JSON only."
                continue
            errors = validate(data, schema)
            if not errors:
                return data
            feedback = ("\n\nPrevious answer was invalid: "
                        + "; ".join(errors) + ". Fix and resend JSON only.")
        return None

    def _try_json(self, base_prompt: str, schema: dict) -> dict | None:
        prompt = base_prompt + _JSON_INSTRUCTIONS
        feedback = ""
        for _ in range(1 + self.max_retries):
            try:
                raw = self.adapter.generate(prompt + feedback,
                                            schema=schema).text
            except AdapterError:
                return None
            try:
                data = json.loads(repair_json(raw))
            except (json.JSONDecodeError, ValueError):
                feedback = "\n\nPrevious answer was invalid JSON. JSON only."
                continue
            errors = validate(data, schema)
            if not errors:
                return data
            feedback = ("\n\nPrevious answer was invalid: "
                        + "; ".join(errors) + ". Fix and resend JSON only.")
        return None

    def _try_kv(self, base_prompt: str, schema: dict,
                *, attempts: int) -> dict | None:
        prompt = base_prompt + _KV_INSTRUCTIONS
        feedback = ""
        for _ in range(attempts):
            try:
                raw = self.adapter.generate(prompt + feedback).text
            except AdapterError:
                return None
            data = parse_kv(raw)
            errors = validate(data, schema)
            if not errors:
                return data
            feedback = ("\n\nPrevious answer was invalid: "
                        + "; ".join(errors) + ". Use the exact line format.")
        return None
