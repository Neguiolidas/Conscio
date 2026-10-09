"""Wrapper defensivo para chamadas a ferramentas externas via HTTP.

Origem:
- OpenTag: agent/parallel_tools.py:50-102
Erros do servidor nunca repassados crus (podem conter credenciais).
Sessões opacas via sha256; URLs limpas sem credenciais; limite de 1 MiB de corpo; sem seguir redirects.
Stdlib apenas (urllib, hashlib).
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ToolFailure:
    """Encapsulamento seguro de falha em ferramenta sem vazamento de detalhes internos."""

    def __init__(self, error_type: str, message: str, http_status: int | None = None) -> None:
        self.error_type = error_type
        self.message = message
        self.http_status = http_status

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": self.message,
            "http_status": self.http_status,
        }

    def __repr__(self) -> str:
        return f"ToolFailure({self.error_type!r}, {self.message!r}, http_status={self.http_status})"


def session_id(*parts: str) -> str:
    """Gera hash sha256 opaco a partir das partes identificadoras para não vazar IDs reais."""
    combined = ":".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def public_url(value: str) -> str:
    """Extrai scheme e hostname[:port] eliminando usuário, senha, caminhos e query parameters."""
    parsed = urllib.parse.urlsplit(value)
    netloc = parsed.netloc.split("@")[-1]  # remove user:pass
    return f"{parsed.scheme}://{netloc}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Handler que aborta redirects automáticos para prevenir redirecionamento opaco."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 45.0,
    headers: dict[str, str] | None = None,
    api_key: str | None = None,
    opener: Any | None = None,
) -> tuple[dict[str, Any] | None, ToolFailure | None]:
    """Realiza chamada POST JSON com proteção contra redirects, limitação de 1 MiB e sanitização de erro."""
    body_bytes = json.dumps(payload).encode("utf-8")

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Conscio-ToolWrap/4.9",
    }
    if headers:
        req_headers.update(headers)
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method="POST")
    active_opener = opener if opener is not None else urllib.request.build_opener(_NoRedirectHandler)

    try:
        with active_opener.open(req, timeout=timeout_s) as response:
            status = getattr(response, "status", 200)
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                return None, ToolFailure("response_too_large", "Response exceeded 1 MiB limit", status)

            try:
                parsed_json = json.loads(data.decode("utf-8"))
                return parsed_json, None
            except Exception:
                return None, ToolFailure("invalid_response", "Invalid JSON received", status)

    except urllib.error.HTTPError as e:
        if e.code == 429:
            return None, ToolFailure("rate_limit", "Rate limit exceeded", 429)
        return None, ToolFailure("http_error", f"HTTP failure status {e.code}", e.code)
    except TimeoutError:
        return None, ToolFailure("timeout", "Request timed out", None)
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            return None, ToolFailure("timeout", "Request timed out", None)
        return None, ToolFailure("network_error", "Network connection failed", None)
    except Exception as e:
        return None, ToolFailure("internal_error", f"Unexpected tool invocation error: {type(e).__name__}", None)
