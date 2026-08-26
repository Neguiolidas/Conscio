# conscio/squads/experts/auditor.py
"""Auditor voice — security heuristics (Ato 1, v4.4).

Deterministic analysis of a decision/artefact for security risks:
hardcoded secrets/credentials, SQL injection patterns, command
injection, unsafe deserialization, and dangerous eval/exec usage.
Pure stdlib, no LLM.
"""
from __future__ import annotations

import re
from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Token hints that suggest hardcoded secrets.
_SECRET_HINT = re.compile(
    r"\b(?:api[_-]?key|secret|password|passwd|token|auth)\b\s*[=:]\s*['\"][A-Za-z0-9_\-]{8,}['\"]",
    re.IGNORECASE,
)
_SK_PREFIX = re.compile(r"\b(?:sk-|pk-|nvapi-|AKIA|ghp_|xox[baprs]-)[A-Za-z0-9_\-]{10,}")

# SQL injection: f-string / string concat / %-format with user input.
_SQL_INJECTION = re.compile(
    r"f['\"`][^'\"`]*\{(?:user_input|input|request_get|form|params|id)\}[^'\"`]*"
    r"|(?:select\s+\*|update\s+|delete\s+from|insert\s+into)[^'\"]*\{"
    r"|(?:select\s+\*|update\s+|delete\s+from|insert\s+into)[^'\"]*%",
    re.IGNORECASE,
)
_SQL_CONCAT = re.compile(
    r"(?:query|sql|stmt)\s*[+=]\s*['\"].*?\{(?:user_input|input|request_get|form|params)\}",
    re.IGNORECASE,
)

# Command injection via shell concatenation.
_CMD_INJECTION = re.compile(r"(?:os\.system|subprocess\.(?:call|run|Popen))\s*\([^)]*f['\"]|(?:os\.system|subprocess).*?\+.*?(?:user_input|input|request)", re.IGNORECASE)

# Dangerous exec/eval/pickle.
_UNSAFE_EVAL = re.compile(r"\b(?:eval|exec|pickle\.loads|yaml\.load)\b\s*\(", re.IGNORECASE)


class AuditorVoice(Voice):
    """Security scrutineer — flags secrets, injection, unsafe parsing."""

    name = "auditor"
    role = "auditor"
    description = (
        "Security heuristics: hardcoded secrets, SQL/command injection, "
        "unsafe eval/deserialization."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        text = ctx.get("question", "") + "\n" + ctx.get("context", "")

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # 1. Hardcoded secret pattern
        sec = _SECRET_HINT.search(text) or _SK_PREFIX.search(text)
        if sec:
            analysis_parts.append("detected hardcoded credential pattern")
            concerns.append(
                "Hardcoded secret/credential detected — move to a secure "
                "secrets store (env var or vault)."
            )

        # 2. SQL injection
        if _SQL_INJECTION.search(text) or _SQL_CONCAT.search(text):
            analysis_parts.append("detected SQL injection risk")
            concerns.append(
                "SQL built from user input — use parameterised queries/"
                "prepared statements."
            )

        # 3. Command injection
        if _CMD_INJECTION.search(text):
            analysis_parts.append("detected command injection risk")
            concerns.append(
                "Shell command built from input — avoid shell=True and "
                "validate/whitelist arguments."
            )

        # 4. Unsafe eval / deserialization
        if _UNSAFE_EVAL.search(text):
            analysis_parts.append("detected unsafe eval/deserialization")
            concerns.append(
                "Unsafe eval/exec/pickle.loads — untrusted input can "
                "execute arbitrary code."
            )

        if not analysis_parts:
            analysis_parts.append("No obvious security regressions detected.")

        analysis = "; ".join(analysis_parts)
        vote = _vote_from_concerns(concerns)
        return VoiceResult(
            role=self.role,
            analysis=analysis,
            concerns=concerns,
            vote=vote,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> VoiceResult:
        """LLM path for deeper threat-modelling review (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the Auditor voice. Review this for security risks "
                f"(secrets, injection, authz gaps, unsafe deserialization). "
                f"Be concise.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("risk", "vulner", "unsafe", "leak",
                                                "injection", "secret", "auth")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            pass
        return result