"""Verificação em 2 camadas: gates determinísticos objetivos antes do juiz subjetivo.

Origem:
- foreman: foreman/verifier.py:196-260 (verify), :95-111 (_gate_is_invalid)
Gates objetivos falhos rejeitam independente do juiz (juiz ainda roda para prover feedback).
Distingue gate inquebrável (SyntaxError, command not found, exit 9009) de falha real (AssertionError).
Stdlib apenas.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable


@dataclasses.dataclass
class GateResult:
    """Resultado individual de um gate de verificação."""
    name: str
    passed: bool
    error: str | None = None


@dataclasses.dataclass
class Verdict:
    """Veredito final consolidado da verificação em 2 camadas."""
    passed: bool
    reason: str
    feedback: str
    gate_invalid: bool
    gates_green: bool


_INVALID_GATE_PATTERNS = [
    re.compile(r"command not found", re.IGNORECASE),
    re.compile(r"exit (code )?(9009|127)", re.IGNORECASE),
    re.compile(r"SyntaxError.*<string>", re.IGNORECASE),
    re.compile(r"ModuleNotFoundError", re.IGNORECASE),
    re.compile(r"No such file or directory", re.IGNORECASE),
]


def is_gate_invalid(gate: GateResult) -> bool:
    """Identifica se a falha do gate decorre de erro do próprio ambiente/definição do teste."""
    if gate.passed or not gate.error:
        return False

    err = gate.error
    return any(p.search(err) for p in _INVALID_GATE_PATTERNS)


def verify(
    checks: list[Callable[[], GateResult]],
    judge: Callable[[list[GateResult]], tuple[bool, str]] | None = None,
) -> Verdict:
    """Executa checagens determinísticas e, se fornecido, juiz de qualidade."""
    results: list[GateResult] = []
    has_invalid_gate = False
    has_real_gate_failure = False

    for check in checks:
        res = check()
        results.append(res)
        if not res.passed:
            if is_gate_invalid(res):
                has_invalid_gate = True
            else:
                has_real_gate_failure = True

    gates_green = not has_real_gate_failure
    
    judge_passed = True
    judge_feedback = ""
    if judge is not None:
        try:
            judge_passed, judge_feedback = judge(results)
        except Exception as e:
            judge_passed = False
            judge_feedback = f"Judge execution failed: {e}"

    passed = gates_green and not has_invalid_gate and judge_passed

    reasons: list[str] = []
    if has_invalid_gate:
        reasons.append("[INVALID GATE DETECTED]")
    if has_real_gate_failure:
        failed_names = [r.name for r in results if not r.passed and not is_gate_invalid(r)]
        reasons.append(f"Objective gates failed: {', '.join(failed_names)}")
    if not judge_passed:
        reasons.append(f"Judge rejected: {judge_feedback}")
    if passed:
        reasons.append("All gates and judge approved.")

    final_reason = "; ".join(reasons)
    return Verdict(
        passed=passed,
        reason=final_reason,
        feedback=judge_feedback,
        gate_invalid=has_invalid_gate,
        gates_green=gates_green,
    )
