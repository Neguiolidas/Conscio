"""Claim idempotente e single-use de aprovações com superação cronológica.

Origem:
- OpenDots: src/server/connection-store.ts:155-175
- OpenTag: app/human-in-the-loop/approval-decisions.ts:10-29
Card velho nunca aprova interrupt novo: novo registro para uma conversa invalida o anterior.
Tokens possuem TTL configurável (default 7 dias).
Stdlib apenas.
"""

from __future__ import annotations

import secrets
import time


class _ApprovalTokenRecord:
    def __init__(self, token: str, created_at: float) -> None:
        self.token = token
        self.created_at = created_at
        self.consumed = False


class ApprovalGate:
    """Portão de aprovações seguras com claim único e expiração temporal."""

    def __init__(self, ttl_seconds: float = 7 * 86400.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._active: dict[str, _ApprovalTokenRecord] = {}

    def register(self, conversation: str) -> str:
        """Gera e registra novo token de aprovação para a conversa, invalidando token prévio."""
        token = secrets.token_urlsafe(32)
        rec = _ApprovalTokenRecord(token=token, created_at=time.time())
        self._active[conversation] = rec
        return token

    def claim(self, conversation: str, token: str) -> bool:
        """Reivindica a aprovação.
        
        Retorna True somente se:
        - O token confere com o token ativo da conversa;
        - Não foi consumido anteriormente;
        - Encontra-se dentro da janela de validade (TTL).
        """
        rec = self._active.get(conversation)
        if rec is None:
            return False

        if rec.token != token:
            # Token superseded or mismatch
            return False

        if rec.consumed:
            return False

        if (time.time() - rec.created_at) > self._ttl_seconds:
            # Expired
            return False

        rec.consumed = True
        return True
