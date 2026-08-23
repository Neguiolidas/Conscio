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
from typing import TYPE_CHECKING, Any

from .adapter import AdapterConnectionError, AdapterError, InferenceAdapter
from .contracts import ActionProposal, proposal_from_dict, validate

# The tier ladder exists for a model that cannot produce structured output. It
# cannot help when the endpoint was never reached: T3 sends the same request to
# the same dead host with different instructions.
#
# A timeout is deliberately NOT in here, though it looks like it belongs. The
# connection was accepted and the request was taken — the host is reachable,
# and what took too long may well be this particular request. Measured on
# NVIDIA NIM: response_format=json_object times out where the same prompt
# without it answers, so T2 stalling is precisely when T3 is worth trying. The
# same reasoning covers a server that answers badly; HTTP 400 for an
# unsupported response_format is the case T3 fixes by sending no schema at all.
_UNREACHABLE = (AdapterConnectionError,)

if TYPE_CHECKING:
    from .intercepter import Intercepter, InterceptionLoop


class GatewayError(Exception):
    """All decode tiers failed for this cycle.

    ``infra`` is True when the failure came from the inference endpoint
    (unreachable, timeout, provider outage, permanent reject) and False when
    the model replied but no tier could decode a valid proposal. The caller
    must not collapse a goal's circuit breaker for an infra failure — a dead
    endpoint is an environment problem, not an intractable goal.
    """

    def __init__(self, message: str, *, infra: bool = False) -> None:
        super().__init__(message)
        self.infra = infra


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


# Enough of the reply to recognise its shape; short enough to log every time.
_RAW_SAMPLE_CHARS = 200

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
                 tier: str | None = None,
                 intercepter: Intercepter | None = None,
                 max_intercept_iterations: int = 3,
                 failure_governor=None):
        self.adapter = adapter
        self.max_retries = max_retries
        self.tier = tier         # explicit "T1"/"T2"/"T3"; None = caps auto
        self.last_tier = ""      # tier that produced (or last tried) decode
        self.last_adapter_error: AdapterError | None = None
        # What the model last said, and why it was rejected. Kept so that a
        # decode failure names its cause instead of only its outcome.
        self.last_raw = ""
        self.last_decode_errors: list[str] = []
        # What the last request_action spent, summed over every tier and retry
        # it took. The ladder can call the model several times for one
        # proposal, so the cost of an action is not the cost of the call that
        # finally decoded — it is all of them.
        self.last_tokens_in = 0
        self.last_tokens_out = 0
        self._token_ledger = None
        # v3.1: failure classification + circuit breaker
        from conscio.failure import FailureGovernor
        self._failure_gov = failure_governor or FailureGovernor(max_consecutive=3)
        # Intercepter integration (v2.7)
        self._loop: InterceptionLoop | None = None
        if intercepter is not None:
            from .intercepter import InterceptionLoop
            self._loop = InterceptionLoop(
                adapter, intercepter,
                max_iterations=max_intercept_iterations,
            )

    def effective_tier(self) -> str:
        """Tier request_action will use: explicit, else adapter caps."""
        if self.tier is not None:
            return self.tier
        caps = self.adapter.capabilities()
        return "T1" if caps.grammar else "T2" if caps.json_mode else "T3"

    def attach_ledger(self, ledger) -> None:
        """v3.1: attach a TokenLedger to record per-task token usage."""
        self._token_ledger = ledger

    def _generate(self, prompt: str, **kwargs: Any) -> Any:
        """Route through InterceptionLoop if present, else call adapter directly.

        T1 (GBNF) always bypasses the loop because the grammar forces JSON
        output — the LLM cannot emit [INTERCEPT: ...] tags under GBNF.
        """
        if self._loop is not None and self.effective_tier() != "T1":
            result = self._loop.generate(prompt, **kwargs)
        else:
            result = self.adapter.generate(prompt, **kwargs)
        self.last_tokens_in += int(getattr(result, "tokens_in", 0) or 0)
        self.last_tokens_out += int(getattr(result, "tokens_out", 0) or 0)
        # v3.1: record token usage if ledger attached
        ledger = getattr(self, "_token_ledger", None)
        if ledger is not None and hasattr(result, "tokens_in"):
            latency = getattr(result, "latency_ms", 0)
            ledger.record(
                model=self.adapter.capabilities().model_name,
                prompt_tokens=result.tokens_in,
                completion_tokens=result.tokens_out,
                duration_seconds=latency / 1000.0 if latency else 0.0,
            )
        return result

    def request_action(self, base_prompt, schema: dict,
                       *, goal_id: str = "",
                       tool_names: list[str] | None = None) -> ActionProposal:
        # v3.1: accept PromptZones — convert to string for downstream tiers.
        # Cache breakpoint sits at the stable/volatile boundary (full_prompt).
        if hasattr(base_prompt, "full_prompt"):
            base_prompt = base_prompt.full_prompt
        self.last_adapter_error = None
        self.last_raw = ""
        self.last_decode_errors = []
        self.last_tokens_in = 0
        self.last_tokens_out = 0
        caps = self.adapter.capabilities()
        tier = self.effective_tier()
        if tier == "T1":
            self.last_tier = "T1"
            data = self._try_grammar(base_prompt, schema, tool_names)
            if data is None and not self._no_lower_tier_can_help():  # one/cycle
                if caps.json_mode:
                    self.last_tier = "T2"
                    data = self._try_json(base_prompt, schema)
                else:
                    self.last_tier = "T3"
                    data = self._try_kv(base_prompt, schema, attempts=1)
        elif tier == "T2":
            self.last_tier = "T2"
            data = self._try_json(base_prompt, schema)
            if data is None and not self._no_lower_tier_can_help():  # T2 -> T3
                self.last_tier = "T3"
                data = self._try_kv(base_prompt, schema, attempts=1)
        else:
            self.last_tier = "T3"
            data = self._try_kv(base_prompt, schema,
                                attempts=1 + self.max_retries)
        if data is None:
            # v3.1: check if failure was PERMANENT — if so, don't try more tiers
            if self.last_adapter_error is not None:
                from conscio.failure import FailureGovernor as _FG
                cls = _FG.classify(self.last_adapter_error)
                if not _FG.should_retry(cls):
                    raise GatewayError(
                        "permanent failure: " + str(self.last_adapter_error),
                        infra=True)
                if not self.last_raw:
                    # Nothing was ever decoded, so "decode failed" would send the
                    # operator to the schema and the model's output format to
                    # explain an unreachable host or a rejected request.
                    raise GatewayError(
                        f"adapter call failed ({cls.value}): "
                        f"{self.last_adapter_error}",
                        infra=True)
            raise GatewayError("all decode tiers failed" + self._decode_detail())
        return proposal_from_dict(data, goal_id=goal_id)

    def _no_lower_tier_can_help(self) -> bool:
        """Would the next tier down just repeat this failure?

        Two cases: the endpoint was never reached, and the request was
        rejected for a reason no rewording changes (auth, content filter).
        Both spend a second call to be told the same thing.
        """
        if self.last_adapter_error is None:
            return False          # a plain decode failure — that is what T3 is for
        if isinstance(self.last_adapter_error, _UNREACHABLE):
            return True
        from conscio.failure import FailureGovernor as _FG
        return not _FG.should_retry(_FG.classify(self.last_adapter_error))

    def _decode_detail(self) -> str:
        """What the model said and why it was rejected, for the error message.

        'all decode tiers failed' on its own cannot distinguish a model that
        answers prose from one that answers JSON with the wrong keys, and the
        operator has no other record: the reply is discarded on the way out.
        Bounded because this string reaches the ledger and the event bus.
        """
        parts: list[str] = []
        if self.last_decode_errors:
            parts.append("last errors: " + "; ".join(self.last_decode_errors[:3]))
        if self.last_raw:
            sample = " ".join(self.last_raw.split())[:_RAW_SAMPLE_CHARS]
            parts.append(f"last reply: {sample!r}")
        if self.last_adapter_error is not None:
            # A tier that answered and a tier that never ran both end up here.
            # Without this the adapter failure is dropped and the message reads
            # as though every tier got a reply it could not parse.
            parts.append(f"adapter error: {self.last_adapter_error}")
        return f" ({', '.join(parts)})" if parts else ""

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
                raw = self._generate(prompt + feedback, schema=schema,
                                            grammar=grammar).text
            except AdapterError as exc:
                # Both arms of the old classification returned None, so the
                # class was computed and discarded. request_action owns that
                # decision now: it can see every tier, this block sees one.
                self.last_adapter_error = exc
                return None
            self.last_raw = raw
            try:
                data = json.loads(repair_json(raw))
            except (json.JSONDecodeError, ValueError):
                self.last_decode_errors = ["not JSON"]
                feedback = "\n\nPrevious answer was invalid JSON. JSON only."
                continue
            errors = validate(data, schema)
            if not errors:
                return data
            self.last_decode_errors = errors
            feedback = ("\n\nPrevious answer was invalid: "
                        + "; ".join(errors) + ". Fix and resend JSON only.")
        return None

    def _try_json(self, base_prompt: str, schema: dict) -> dict | None:
        prompt = base_prompt + _JSON_INSTRUCTIONS
        feedback = ""
        for _ in range(1 + self.max_retries):
            try:
                raw = self._generate(prompt + feedback,
                                            schema=schema).text
            except AdapterError as exc:
                # Both arms of the old classification returned None, so the
                # class was computed and discarded. request_action owns that
                # decision now: it can see every tier, this block sees one.
                self.last_adapter_error = exc
                return None
            self.last_raw = raw
            try:
                data = json.loads(repair_json(raw))
            except (json.JSONDecodeError, ValueError):
                self.last_decode_errors = ["not JSON"]
                feedback = "\n\nPrevious answer was invalid JSON. JSON only."
                continue
            errors = validate(data, schema)
            if not errors:
                return data
            self.last_decode_errors = errors
            feedback = ("\n\nPrevious answer was invalid: "
                        + "; ".join(errors) + ". Fix and resend JSON only.")
        return None

    def _try_kv(self, base_prompt: str, schema: dict,
                *, attempts: int) -> dict | None:
        prompt = base_prompt + _KV_INSTRUCTIONS
        feedback = ""
        for _ in range(attempts):
            try:
                raw = self._generate(prompt + feedback).text
            except AdapterError as exc:
                # Both arms of the old classification returned None, so the
                # class was computed and discarded. request_action owns that
                # decision now: it can see every tier, this block sees one.
                self.last_adapter_error = exc
                return None
            self.last_raw = raw
            data = parse_kv(raw)
            errors = validate(data, schema)
            if not errors:
                return data
            self.last_decode_errors = errors
            feedback = ("\n\nPrevious answer was invalid: "
                        + "; ".join(errors) + ". Use the exact line format.")
        return None
