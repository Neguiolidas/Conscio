"""Hub provider catalog + per-type model discovery.

Discovery (probe_models) lives HERE, not in adapter_config — adapter_config's
job is building an adapter; listing models is a Hub concern. Stdlib urllib only."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from . import config as _config

BUILTIN = list(_config.KNOWN_TYPES)

# Seed for the free-text datalist when a provider has no listing endpoint
# (anthropic) or a probe fails. Not exhaustive — the field stays free-text.
KNOWN_MODELS: dict[str, list[str]] = {
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"],
    "openai": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash"],
    "ollama": ["llama3.1", "qwen2.5"],
    "openai-compat": [],
    "lmstudio": [],
}

# Default base_url per type — MUST mirror adapter_config.build_adapter_from_config.
_DEFAULT_BASE_URL = {
    "lmstudio": "http://localhost:1234/v1",
    "ollama": "http://localhost:11434",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "openai-compat": "http://localhost:8000/v1",
}

_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_TTL = 60.0


def plugins() -> list[str]:
    """Names of conscio.adapters plugins (reuses existing stdlib discovery)."""
    from ..plugins import discover_adapters
    return sorted(discover_adapters().keys())


def catalog(cfg: dict) -> dict:
    """builtin types + plugin names + redacted custom providers."""
    custom = cfg.get("providers", {})
    if not isinstance(custom, dict):
        custom = {}
    return {
        "builtin": list(BUILTIN),
        "plugins": plugins(),
        "custom": {k: _config._redact_block(v) if isinstance(v, dict) else v
                   for k, v in custom.items()},
    }


def resolve_provider(cfg: dict, provider: str) -> dict:
    """Turn a ?provider= param (custom NAME or builtin TYPE) into a provider_cfg.
    Raises KeyError if neither."""
    custom = cfg.get("providers", {})
    if isinstance(custom, dict) and provider in custom:
        return dict(custom[provider])
    if provider in _DEFAULT_BASE_URL:
        return {"type": provider, "base_url": _DEFAULT_BASE_URL[provider]}
    raise KeyError(provider)


def _get_json(url: str, *, headers: dict | None = None, timeout: float = 10) -> Any:
    """GET + parse JSON. Raises on non-2xx / bad JSON. NEVER log `url` (it may
    carry a ?key= secret for gemini)."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fallback(atype: str) -> dict:
    return {"models": list(KNOWN_MODELS.get(atype, [])),
            "source": "fallback", "probed": False}


def _parse(atype: str, data: Any) -> list[str]:
    if atype in ("openai", "openai-compat", "lmstudio"):
        return [m["id"] for m in data.get("data", []) if "id" in m]
    if atype == "ollama":
        return [m["name"] for m in data.get("models", []) if "name" in m]
    if atype == "gemini":
        return [m["name"].split("/", 1)[-1]
                for m in data.get("models", []) if "name" in m]
    return []


def probe_models(provider_cfg: dict, *, refresh: bool = False) -> dict:
    """Discover model ids for a provider, or a free-text fallback list. Cached."""
    atype = provider_cfg.get("type", "")
    base = (provider_cfg.get("base_url") or _DEFAULT_BASE_URL.get(atype, "")).rstrip("/")
    if atype == "anthropic" or not base:
        return _fallback(atype)
    cache_key = (atype, base)
    if not refresh:
        hit = _CACHE.get(cache_key)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL:
            return dict(hit[1])
    try:
        if atype == "ollama":
            url = f"{base}/api/tags"
        elif atype == "gemini":
            env = provider_cfg.get("api_key_env")
            key = (os.environ.get(env, "") if env else "")
            url = f"{base}/v1beta/models" + (f"?key={key}" if key else "")
        else:                                          # openai / openai-compat / lmstudio
            url = f"{base}/models"
        models = _parse(atype, _get_json(url))
    except (OSError, ValueError, urllib.error.URLError, KeyError, TypeError, AttributeError):
        return _fallback(atype)
    result = {"models": models, "source": "api", "probed": True}
    _CACHE[cache_key] = (time.monotonic(), result)
    return result
