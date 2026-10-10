"""pipeline: a cola que conecta os 21 módulos robustez em 5 fluxos padrão.

Os módulos sozinhos são peças; este arquivo é o encaixe. Nenhuma lógica
nova de domínio aqui — só composição, na ordem que as garantias exigem.

Fluxos:
  1. fetch_external — fronteira: ssrf → ingest → normalize → untrusted → secrets
  2. call_tool      — execução: singleflight → toolwrap → outcomes_ext (+ retry)
  3. tracked_work   — coordenação: stateledger → approval → crash → transition
  4. judge          — julgamento: logitjev → decision → verifier → arbiter
  5. refresh_doc    — memória: deltaops → retractions

Transversal: sanitize_history_for_llm (history) e safe_log_context (secrets).

Stdlib apenas.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from . import (
    approval,
    arbiter,
    crash,
    decision,
    deltaops,
    history,
    ingest,
    logitjev,
    normalize,
    outcomes_ext,
    retractions,
    retry,
    secrets,
    singleflight,
    ssrf,
    stateledger,
    toolwrap,
    untrusted,
    verifier,
)

# ---------------------------------------------------------------------------
# Fluxo 1 — Fronteira: dado externo entra validado, limitado, envelopado
# ---------------------------------------------------------------------------

def fetch_external(
    url: str,
    *,
    timeout_s: float = 45.0,
    max_bytes: int = 1024 * 1024,
    default_source: str = "unknown",
    stats: dict | None = None,
) -> list[dict[str, str]]:
    """Busca conteúdo externo com todas as garantias da fronteira.

    Ordem importa: ssrf primeiro (nada sai sem URL validada) e de novo em cada
    salto de redirect, ingest com limite de bytes, normalize estrutura,
    untrusted envelopa, secrets redige.
    Cada registro sai com ``content`` envelopado como dado não-confiável.

    Limite conhecido: a validação resolve o DNS e o urllib resolve de novo ao
    conectar, então um DNS rebinding entre as duas resoluções não é coberto.
    """
    ssrf.assert_safe_url(url)
    text = ingest.fetch_text(
        url, timeout_s=timeout_s, max_bytes=max_bytes,
        validate_redirect=ssrf.assert_safe_url,
    )
    records = normalize.normalize_records(text, default_source=default_source, stats=stats)
    out: list[dict[str, str]] = []
    for rec in records:
        clean = secrets.redact_tokens(rec["content"])
        out.append({**rec, "content": untrusted.mark_untrusted(clean, source=rec["provenance"])})
    return out


# ---------------------------------------------------------------------------
# Fluxo 2 — Execução: chamada de ferramenta com dedup, outcome honesto e retry
# ---------------------------------------------------------------------------

def failure_to_outcome(failure: toolwrap.ToolFailure) -> outcomes_ext.Outcome:
    """Converte ToolFailure em Outcome honesto.

    Regra capital: erro de rede ou interno vira UNKNOWN — a requisição pode
    ter sido executada do outro lado, então retry cego arrisca duplicar
    escrita (a distinção failed vs unknown do outcomes_ext).
    """
    kind = failure.error_type
    if kind == "rate_limit":
        return outcomes_ext.Outcome.RATE_LIMITED
    if kind == "timeout":
        return outcomes_ext.Outcome.TIMEOUT
    if kind == "http_error":
        if failure.http_status == 403:
            return outcomes_ext.Outcome.PERMISSION_DENIED
        if failure.http_status == 400:
            return outcomes_ext.Outcome.INVALID_REQUEST
        return outcomes_ext.Outcome.FAILED
    if kind in ("response_too_large", "invalid_response"):
        return outcomes_ext.Outcome.FAILED
    # network_error, internal_error e desconhecidos: UNKNOWN, nunca retry direto
    return outcomes_ext.Outcome.UNKNOWN


def call_tool(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    timeout_s: float = 45.0,
    headers: dict[str, str] | None = None,
    flight: singleflight.SingleFlight | None = None,
    flight_key: str | None = None,
    retry_state: dict[str, Any] | None = None,
    task_id: str = "",
) -> tuple[dict[str, Any] | None, outcomes_ext.Outcome, toolwrap.ToolFailure | None]:
    """POST JSON com coalescência, outcome tipado e registro de retry.

    Retorna (data, outcome, failure). Se ``flight`` + ``flight_key`` forem
    dados, chamadas concorrentes sob a mesma chave compartilham uma única
    execução. Se ``retry_state`` for dado (retry.new_state()), sucessos e
    falhas alimentam o backoff exponencial.
    """
    def _do() -> tuple[dict[str, Any] | None, outcomes_ext.Outcome, toolwrap.ToolFailure | None]:
        data, failure = toolwrap.call_json(
            url, payload, timeout_s=timeout_s, headers=headers, api_key=api_key
        )
        if failure is None:
            return data, outcomes_ext.Outcome.SUCCESS, None
        return None, failure_to_outcome(failure), failure

    result = flight.run(flight_key, _do) if (flight is not None and flight_key) else _do()

    if retry_state is not None:
        _, outcome, _ = result
        if outcome == outcomes_ext.Outcome.SUCCESS:
            retry.record_success(retry_state)
        else:
            retry.record_failure(
                retry_state, task_id or "tool",
                detail=outcomes_ext.describe_outcome(outcome),
            )

    return result


# ---------------------------------------------------------------------------
# Fluxo 3 — Coordenação: trabalho rastreado com aprovação e finalização honesta
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def tracked_work(
    ledger: stateledger.StateLedger,
    item_id: str,
    worker_id: str,
    lease_seconds: float,
    *,
    payload: Any = "",
    gate: approval.ApprovalGate | None = None,
    conversation: str = "",
    approval_token: str = "",
    done_state: str = "done",
    fail_state: str = "failed",
) -> Generator[dict[str, Any], None, None]:
    """Ciclo completo de um item de trabalho.

    1. add_item (idempotente) → 2. claim atômico do próprio item_id (os
       outros itens da fila ficam intocados) → 3. approval.claim (se gate) →
       4. crash.tracked_run fecha eventos abertos em exceção →
       5. transition para done_state / fail_state.

    O mapa de transições do ledger precisa autorizar
    claimed_state → done_state e claimed_state → fail_state.
    """
    try:
        ledger.add_item(item_id, payload)
    except sqlite3.IntegrityError:
        pass  # item já existe: segue para reivindicar

    claimed = ledger.claim(item_id, worker_id, lease_seconds)
    if claimed is None:
        raise RuntimeError(f"Não foi possível reivindicar o item {item_id!r}")

    if gate is not None:
        if not gate.claim(conversation or item_id, approval_token):
            ledger.release(item_id, worker_id)
            raise PermissionError(f"Aprovação negada/expirada para {item_id!r}")

    with crash.tracked_run():
        try:
            yield claimed
        except Exception:
            try:
                ledger.transition(item_id, fail_state, worker_id)
            except Exception:
                pass  # a exceção original é a que importa
            raise
        ledger.transition(item_id, done_state, worker_id)


# ---------------------------------------------------------------------------
# Fluxo 4 — Julgamento: decisão barata → gates objetivos → apelação fail-closed
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def judge(
    question: decision.Choice | decision.Score | decision.Noul,
    state: str,
    jev: logitjev.OpenAICompatJev,
    *,
    gates: list[Callable[[], verifier.GateResult]] | None = None,
    judge_fn: Callable[[list[verifier.GateResult]], tuple[bool, str]] | None = None,
    arbiter_judge: Callable[[str], dict[str, Any]] | None = None,
    task_id: str | None = None,
    evidence_path: str | None = None,
    arb: arbiter.Arbiter | None = None,
) -> dict[str, Any]:
    """Pipeline de julgamento em camadas.

    logitjev.ask decide num forward pass (honesto: levanta se o label não
    aparece no top-k) → verifier.verify roda gates objetivos antes do juiz
    subjetivo → arbiter.arbitrate julga apelação, fail-closed, teto de 1
    apelação por task_id.

    O teto vive no ``Arbiter``: quem pede apelação passa o mesmo ``arb`` em
    todas as chamadas. Sem ele, a apelação é recusada (ValueError) em vez de
    criar um árbitro novo a cada chamada, que zeraria o teto em silêncio.
    """
    appealing = arbiter_judge is not None or task_id is not None
    if appealing and arb is None:
        raise ValueError("judge: appeals need a long-lived Arbiter (arb=) to enforce the cap")
    answer = jev.ask(question, state)
    verdict = verifier.verify(gates or [], judge_fn)
    result: dict[str, Any] = {"answer": answer, "verdict": verdict}
    if appealing and arb is not None:
        claim: dict[str, Any] = {"answer": answer, "evidence_path": evidence_path}
        result["ruling"] = arb.arbitrate(
            claim, verdict, _read_file, task_id=task_id, judge=arbiter_judge
        )
    return result


# ---------------------------------------------------------------------------
# Fluxo 5 — Memória: aplica operações endereçadas e poda o que foi retraído
# ---------------------------------------------------------------------------

def refresh_doc(
    doc: dict[str, Any],
    ops: list[dict[str, Any]],
    live_fact_ids: set[str],
    *,
    fact_types: frozenset = retractions.DEFAULT_FACT_TYPES,
) -> dict[str, Any]:
    """Aplica delta ops e remove citações de fatos retraídos.

    Retorna {"document", "status" ("valid" | "pruned"), "applied", "skipped",
    "retracted_ids"}. O doc original nunca é mutado (deltaops copia).
    """
    applied = deltaops.apply_ops(doc, ops)
    document = applied["document"]
    _, retracted = retractions.partition_retracted([document], live_fact_ids, fact_types)
    retracted_ids: list[str] = []
    if retracted:
        before = set(retractions._cited_ids(document))
        document = retractions.prune_based_on(document, live_fact_ids, fact_types)
        retracted_ids = sorted(before - set(retractions._cited_ids(document)))
        status = "pruned"
    else:
        status = "valid"
    return {
        "document": document,
        "status": status,
        "applied": applied["applied"],
        "skipped": applied["skipped"],
        "retracted_ids": retracted_ids,
    }


# ---------------------------------------------------------------------------
# Transversal
# ---------------------------------------------------------------------------

def sanitize_history_for_llm(
    history_list: list[dict[str, Any]],
    answered_elsewhere: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Remove tool calls órfãos antes de cada chamada ao LLM (puro, não muta)."""
    return history.sanitize_history(history_list, answered_elsewhere)


def safe_log_context(text: str) -> str:
    """Primeira linha, sem segredos, cap 180 chars — para qualquer borda de log."""
    return secrets.sanitize_reason(text)


__all__ = [
    "call_tool",
    "failure_to_outcome",
    "fetch_external",
    "judge",
    "refresh_doc",
    "safe_log_context",
    "sanitize_history_for_llm",
    "tracked_work",
]
