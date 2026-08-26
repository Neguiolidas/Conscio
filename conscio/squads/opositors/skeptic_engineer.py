# conscio/squads/opositors/skeptic_engineer.py
"""Skeptic Engineer voice — hunts over-engineering (Ato 4, v4.4).

Question every technical decision. Why Tauri? Why IPC? Why 5 frameworks
for a CRUD app? The Skeptic Engineer detects over-engineering,
unnecessary complexity, and ego-driven architecture.

Tone boundary: questions DECISIONS and ARCHITECTURE, never people.
"""
from __future__ import annotations

from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Over-engineering indicators.
_OVERENGINEER_TOKENS = (
    "kubernetes",
    "istio",
    "microservices",
    "event sourcing",
    "cqrs",
    "graphql",
    "apollo",
    "next.js",
    "5 frameworks",
    "distributed",
    "service mesh",
    "sidecar",
)

# IPC / unnecessary indirection.
_IPC_TOKENS = (
    "ipc bridge",
    "tauri + ipc",
    "webview",
    "separate rust backend",
    "ffi boundary",
    "separate process",
    "inter-process",
    "rpc for",
)

# Framework bloat.
_BLOAT_TOKENS = (
    "5 frameworks",
    "using react, redux",
    "react, redux, graphql, apollo",
    "multiple frameworks",
    "framework soup",
    "dependency hell",
)

# Reasonable / simple stack (positive signal — should NOT flag).
_REASONABLE = (
    "simple stack",
    "fastapi + postgres",
    "redis",
    "simple crud",
    "straightforward",
)


class SkepticEngineerVoice(Voice):
    """Hunts over-engineering, unnecessary complexity, ego-driven architecture."""

    name = "skeptic_engineer"
    role = "skeptic_engineer"
    description = (
        "Skeptic Engineer: questions every technical decision, hunts "
        "over-engineering, unnecessary complexity, ego-driven architecture."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = (ctx.get("question", "") or "").lower()
        context = (ctx.get("context", "") or "").lower()
        blob = f"{question}\n{context}"

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # Is this a simple task with a complex stack?
        is_simple_task = any(
            w in blob
            for w in ("hello world", "simple crud", "todo app", "simple app", "basic")
        )
        is_complex_stack = any(t in blob for t in _OVERENGINEER_TOKENS)
        if is_simple_task and is_complex_stack:
            analysis_parts.append("simple task, complex stack")
            concerns.append(
                "Over-engineering detected: you're deploying Kubernetes for "
                "a hello world. Is the complexity justified, or is this "
                "resume-driven development?"
            )
        elif is_complex_stack:
            analysis_parts.append("complex stack detected")
            concerns.append(
                "Complex stack detected — justify each component or this "
                "looks like engineering for engineering's sake."
            )

        # IPC / unnecessary indirection
        ipc_hits = [t for t in _IPC_TOKENS if t in blob]
        if ipc_hits:
            analysis_parts.append(f"detected IPC/indirection: {', '.join(ipc_hits[:3])}")
            concerns.append(
                "Unnecessary IPC detected — why add a process boundary when "
                "a function call would do? What does this indirection buy you?"
            )

        # Framework bloat
        bloat_hits = [t for t in _BLOAT_TOKENS if t in blob]
        if bloat_hits:
            analysis_parts.append(f"detected framework bloat: {', '.join(bloat_hits[:3])}")
            concerns.append(
                "Framework bloat detected — each dependency is a tax on "
                "build time, bundle size, and maintainability. Prove each "
                "one earns its place."
            )

        if not analysis_parts:
            analysis_parts.append(
                "No obvious over-engineering detected — refreshingly simple."
            )

        analysis = "; ".join(analysis_parts)
        vote = _vote_from_concerns(concerns)
        return VoiceResult(
            role=self.role,
            analysis=analysis,
            concerns=concerns,
            vote=vote,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> VoiceResult:
        """LLM path for deeper architecture review (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the Skeptic Engineer. Question every technical "
                f"decision. Hunt over-engineering, unnecessary complexity, "
                f"and ego-driven architecture. Ask 'why?' about each "
                f"component. Be concise.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("why", "over-engineer", "complex", "unnecessary", "simplify")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            pass
        return result