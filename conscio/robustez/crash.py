"""Finalização honesta e encerramento de eventos abertos em caso de crash.

Origem:
- OpenTag: agent/agui.py:262-329 (OpenEvents)
Garante que eventos abertos (tool calls -> mensagens -> steps) sejam encerrados na ordem reversa
quando ocorre uma falha ou exceção.
Context manager tracked_run nunca engole a exceção original: grava o fechamento e re-levanta o erro.
Stdlib apenas.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any


class OpenEvents:
    """Rastreador de eventos abertos durante um ciclo de execução."""

    def __init__(self) -> None:
        self._events: list[Any] = []
        self.last_closing: list[Any] = []

    def track(self, event: Any) -> None:
        """Registra um evento que foi aberto."""
        self._events.append(event)

    def close(self) -> list[Any]:
        """Fecha todos os eventos abertos na ordem inversa (do mais interno pro mais externo)."""
        # Order priority: tool_calls -> messages -> steps
        # If events are structured dicts with "type":
        def sort_key(item: Any) -> int:
            if isinstance(item, dict) and "type" in item:
                t = item["type"]
                if t == "tool_call":
                    return 0
                if t == "message":
                    return 1
                if t == "step":
                    return 2
            return 3

        # Sort according to priority while preserving reverse insertion
        indexed = list(enumerate(self._events))
        # Reverse insertion order first
        indexed.reverse()
        # Sort stable by hierarchy: tool_call (0) before message (1) before step (2)
        indexed.sort(key=lambda pair: sort_key(pair[1]))

        closed = [item for _, item in indexed]
        self.last_closing = list(closed)
        self._events.clear()
        return closed


def close_open_events(
    steps: list[str],
    messages: list[str],
    tool_calls: list[str],
) -> list[str]:
    """Fecha eventos a partir de listas separadas na ordem reversa estrita: tool_calls -> messages -> steps."""
    result: list[str] = []
    result.extend(reversed(tool_calls))
    result.extend(reversed(messages))
    result.extend(reversed(steps))
    return result


@contextlib.contextmanager
def tracked_run(tracker: OpenEvents | None = None) -> Generator[OpenEvents, None, None]:
    """Context manager que fecha eventos abertos em caso de exceção e re-levanta o erro original."""
    tr = tracker if tracker is not None else OpenEvents()
    try:
        yield tr
    except Exception:
        tr.close()
        raise
