"""Coalescência de requisições concorrentes sob a mesma chave (SingleFlight pattern).

Origem:
- OpenDots: src/server/page-service.ts:38-95, src/server/voice.ts:170-180
Chamadas concorrentes com a mesma chave compartilham UMA execução única.
Falhas removem a chave imediatamente do mapa para permitir nova tentativa.
Stdlib apenas.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class _FlightCall:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.val: Any = None
        self.err: Exception | None = None


class SingleFlight:
    """Gerenciador de coalescência de requisições síncronas e assíncronas."""

    def __init__(self) -> None:
        self._sync_lock = threading.Lock()
        self._sync_calls: dict[str, _FlightCall] = {}

        self._async_lock = asyncio.Lock()
        self._async_tasks: dict[str, asyncio.Task[Any]] = {}

    def run(self, key: str, fn: Callable[[], T]) -> T:
        """Executa fn coalescendo chamadas em threads concorrentes sob a mesma chave."""
        with self._sync_lock:
            if key in self._sync_calls:
                call = self._sync_calls[key]
                first = False
            else:
                call = _FlightCall()
                self._sync_calls[key] = call
                first = True

        if first:
            try:
                call.val = fn()
            except Exception as e:
                call.err = e
            finally:
                with self._sync_lock:
                    self._sync_calls.pop(key, None)
                call.event.set()
        else:
            call.event.wait()

        if call.err is not None:
            raise call.err
        return call.val

    async def arun(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Versão assíncrona para corrotinas concorrentes sob a mesma chave."""
        async with self._async_lock:
            if key in self._async_tasks:
                task = self._async_tasks[key]
            else:
                async def _runner() -> T:
                    try:
                        return await fn()
                    finally:
                        async with self._async_lock:
                            self._async_tasks.pop(key, None)

                task = asyncio.create_task(_runner())
                self._async_tasks[key] = task

        return await task
