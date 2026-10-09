"""Arbitragem de apelação para decisões do Verifier (fail-closed por desenho).

Origem:
- foreman: foreman/arbiter.py:130-180 (Arbiter.rule)
Regras capitais:
- Gate objetivo falho não é disputável: uphold sem consultar o juiz.
- A evidência é relida diretamente da fonte (read_evidence) - nunca se confia no claim.
- Fail closed: juiz ausente, formato indecifrável ou quebra resulta em uphold (empate favorece o verifier).
- Teto de exatamente 1 apelação por task_id.
Stdlib apenas.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from conscio.robustez.verifier import Verdict


@dataclasses.dataclass
class Ruling:
    """Decisão da arbitragem de apelação."""
    overturned: bool
    reason: str
    upheld: bool


class Arbiter:
    """Árbitro de apelações com modelo fail-closed."""

    def __init__(self) -> None:
        self._appealed_tasks: set[str] = set()

    def reset_appeals(self) -> None:
        """Limpa histórico de apelações (usado para testes ou reset de sessão)."""
        self._appealed_tasks.clear()

    def arbitrate(
        self,
        claim: dict[str, Any],
        verifier_verdict: Verdict,
        read_evidence: Callable[[str], str],
        task_id: str | None = None,
        judge: Callable[[str], dict[str, Any]] | None = None,
    ) -> Ruling:
        """Julga uma apelação contra o veredito do Verifier."""
        # 1. Limite de apelação por task
        if task_id:
            if task_id in self._appealed_tasks:
                return Ruling(
                    overturned=False,
                    reason="Appeal limit exceeded for task: only one appeal permitted.",
                    upheld=True,
                )
            self._appealed_tasks.add(task_id)

        # 2. Gate objetivo falho não é disputável
        if not verifier_verdict.gates_green:
            return Ruling(
                overturned=False,
                reason="Objective gates failed; non-disputable condition.",
                upheld=True,
            )

        # 3. Reler evidência fresca da fonte
        evidence_content = ""
        evidence_path = claim.get("evidence_path")
        if evidence_path:
            try:
                evidence_content = read_evidence(str(evidence_path))
            except Exception as e:
                evidence_content = f"[Failed reading evidence: {e}]"

        # 4. Falha se nenhum juiz fornecido (fail-closed)
        if judge is None:
            return Ruling(
                overturned=False,
                reason="No arbiter judge configured; fail-closed upheld verdict.",
                upheld=True,
            )

        # 5. Consulta ao juiz
        prompt = (
            f"Claim: {claim}\n"
            f"Prior Reason: {verifier_verdict.reason}\n"
            f"Evidence: {evidence_content}\n"
        )
        try:
            decision = judge(prompt)
            if not isinstance(decision, dict):
                return Ruling(
                    overturned=False,
                    reason="Invalid judge decision format; fail-closed upheld.",
                    upheld=True,
                )

            ruling_str = str(decision.get("ruling", "")).lower()
            reason_str = str(decision.get("reason", "No reason provided"))

            if ruling_str == "overturn":
                return Ruling(
                    overturned=True,
                    reason=reason_str,
                    upheld=False,
                )
            else:
                return Ruling(
                    overturned=False,
                    reason=reason_str,
                    upheld=True,
                )
        except Exception as e:
            return Ruling(
                overturned=False,
                reason=f"Arbiter judge evaluation failed ({e}); fail-closed upheld.",
                upheld=True,
            )
