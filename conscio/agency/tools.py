# conscio/agency/tools.py
"""
ToolRegistry — the local action surface (spec section 5.4).

Local Python callables only. No network tools in core (safety rule R7);
no shell execution in this repository at all (lives in the sibling
package conscio-shell). Filesystem tools are confined to a mandatory
sandbox root.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .contracts import ToolResult

MAX_WRITE_BYTES = 1_000_000


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., str]
    params: dict[str, dict]
    risk: Risk
    description: str
    precheck: Callable[[dict], str | None] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, name: str, fn: Callable[..., str], *,
                 params: dict[str, dict], risk: Risk,
                 description: str,
                 precheck: Callable[[dict], str | None] | None = None) -> None:
        if name in self._tools:
            raise ValueError(f"tool '{name}' already registered")
        self._tools[name] = ToolSpec(name, fn, params, risk, description,
                                     precheck)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    _RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}

    def catalog_text(self, max_tools: int | None = None) -> str:
        """Compact tool catalog for the actor prompt.

        max_tools (from the ModelProfile, F3) caps what a small model
        sees: safest risks first, then alphabetical. None = full catalog
        in registration order (F1 behavior).
        """
        specs = list(self._tools.values())
        if max_tools is not None and len(specs) > max_tools:
            specs = sorted(specs, key=lambda s: (self._RISK_ORDER[s.risk],
                                                 s.name))[:max_tools]
        lines = []
        for spec in specs:
            args = ", ".join(f"{k}:{v.get('type', 'str')}"
                             for k, v in spec.params.items())
            lines.append(f"- {spec.name}({args}) [{spec.risk.value}] "
                         f"— {spec.description}")
        return "\n".join(lines)

    def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(ok=False, output="",
                              error=f"unknown tool '{name}'")
        start = time.monotonic()
        try:
            output = spec.fn(**args)
            ok, error = True, ""
        except Exception as exc:  # tool failures must never crash the engine
            output = ""
            ok = False
            error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}"
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(ok=ok, output=str(output), error=error,
                          duration_ms=duration)


# ── built-ins ───────────────────────────────────────────────────────────

def _resolve_sandboxed(root: Path, path: str) -> Path:
    candidate = (root / path).resolve()
    root = root.resolve()
    if not candidate.is_relative_to(root):
        raise PermissionError(f"path escapes sandbox root: {path}")
    return candidate


def make_default_registry(*, sandbox_root: Path,
                          content_store: Any = None,
                          event_bus: Any = None,
                          goal_generator: Any = None) -> ToolRegistry:
    sandbox_root = Path(sandbox_root)
    sandbox_root.mkdir(parents=True, exist_ok=True)
    reg = ToolRegistry()

    def _fs_precheck(args: dict) -> str | None:
        """Deterministic sandbox check — runs before the Skeptic (F2)."""
        try:
            _resolve_sandboxed(sandbox_root, str(args.get("path", "")))
        except (PermissionError, ValueError, OSError) as exc:
            return str(exc)
        return None

    def fs_read(path: str) -> str:
        target = _resolve_sandboxed(sandbox_root, path)
        return target.read_text(encoding="utf-8")

    def fs_write(path: str, content: str) -> str:
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ValueError(f"content exceeds size cap ({MAX_WRITE_BYTES}B)")
        target = _resolve_sandboxed(sandbox_root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    reg.register("fs_read", fs_read,
                 params={"path": {"type": "str", "required": True}},
                 risk=Risk.LOW, description="read a file inside the sandbox",
                 precheck=_fs_precheck)
    reg.register("fs_write", fs_write,
                 params={"path": {"type": "str", "required": True},
                         "content": {"type": "str", "required": True}},
                 risk=Risk.MEDIUM,
                 description="write a file inside the sandbox",
                 precheck=_fs_precheck)

    if content_store is not None:
        def memory_note(text: str) -> str:
            content_store.index(label="agency_note", content=text,
                                category="external")
            return "noted"
        reg.register("memory_note", memory_note,
                     params={"text": {"type": "str", "required": True}},
                     risk=Risk.LOW,
                     description="store a note in long-term memory")

    if event_bus is not None:
        def emit_event(text: str) -> str:
            event_bus.emit(type="tool_call", category="external",
                           data={"action": "emit_event", "text": text})
            return "emitted"
        reg.register("emit_event", emit_event,
                     params={"text": {"type": "str", "required": True}},
                     risk=Risk.LOW,
                     description="broadcast an event on the internal bus")

    if goal_generator is not None:
        def goal_update(action: str, goal_id: str) -> str:
            if action == "complete":
                ok = goal_generator.complete_goal(goal_id)
            else:
                ok = goal_generator.cancel_goal(goal_id)
            if not ok:
                raise ValueError(f"goal '{goal_id}' not found or not active")
            return f"goal {goal_id}: {action} ok"
        reg.register(
            "goal_update", goal_update,
            params={"action": {"type": "str", "required": True,
                               "enum": ["complete", "cancel"]},
                    "goal_id": {"type": "str", "required": True}},
            risk=Risk.MEDIUM,
            description="complete or cancel one of the agent's goals")

    return reg
