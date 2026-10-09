"""Backoff exponencial e deduplicação de alertas por streak de falhas.

Origem:
- openmuse: apps/server/src/engine/service.ts:816-870, :905-960
Stdlib apenas.
"""

from __future__ import annotations

from typing import Any


def new_state() -> dict[str, int]:
    """Cria novo estado de rastreamento de retry."""
    return {"failures": 0, "failure_streak": 0}


def record_failure(state: dict[str, Any], task_id: str, detail: str = "") -> dict[str, Any]:
    """Registra falha, calcula backoff exponencial em minutos, detecta streak e emite alert_key."""
    state["failures"] = state.get("failures", 0) + 1
    state["failure_streak"] = state.get("failure_streak", 0) + 1

    # Backoff: min(60, 2^failures) minutos
    failures_cnt = state["failures"]
    backoff_min = min(60, 2 ** failures_cnt)
    
    # Pausa após 5 consecutivas
    paused = failures_cnt >= 5
    status = "paused" if paused else "retry"
    alert_key = f"watch-error:{task_id}:{state['failure_streak']}:{status}"

    return {
        "state": state,
        "backoff_minutes": backoff_min,
        "paused": paused,
        "alert_key": alert_key,
        "detail": detail,
    }


def record_success(state: dict[str, Any]) -> dict[str, Any]:
    """Zera contador de failures transitórias mantendo o total de failure_streak."""
    state["failures"] = 0
    return state
