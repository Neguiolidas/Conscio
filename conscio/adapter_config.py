# conscio/adapter_config.py
"""Shared config loader + adapter builder (v2.0.1).

Extracted verbatim from daemon.py so both the daemon and the MCP server build
the same 6 built-in adapter types from ~/.config/conscio/config.json. No
behavior change for the daemon."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("conscio.adapter_config")

_CONFIG_PATHS = [
    Path.home() / ".config" / "conscio" / "config.json",
    Path.home() / ".conscio" / "config.json",
]


def load_config() -> dict:
    """Load the first existing conscio config file. Returns {} on failure."""
    for path in _CONFIG_PATHS:
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def _read_key_file(path: str, name: str) -> str:
    """Read the key for ``name`` from a ``NOME=valor``-per-line file (BUG-38).

    Strict name match: only a line whose key equals ``name`` yields a value.
    No match, missing file, or unreadable file -> "". Never raises.
    """
    if not name or not path:
        return ""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if value:
            return value
    return ""


def resolve_api_key(env_name: str, *, key_file: str | None = None) -> str:
    """Resolve an API key by precedence: env -> Hub vault -> key_file.

    Never raises; returns "" if not found.
    """
    if not env_name:
        return ""
    # 1. Environment variable
    val = os.environ.get(env_name, "")
    if val:
        return val
    # 2. Hub vault (lazy import: hub.config imports adapter_config)
    try:
        from .hub.config import vault_load
        vault_val = vault_load(env_name)
        if vault_val:
            return vault_val
    except Exception:
        pass
    # 3. Key file
    if key_file:
        try:
            return _read_key_file(key_file, env_name)
        except Exception:
            return ""
    return ""


def build_adapter_from_config(cfg: dict, *,
                              fallback_model: str) -> tuple[Any, Any]:
    """Build an InferenceAdapter from the config's 'adapter' block.

    Returns (adapter, adapter_type_str) or (None, None) if no config adapter.
    Config adapter keys: type (required), model, api_key, base_url.
    CLI args always override config values.
    """
    adapter_cfg = cfg.get("adapter")
    if not isinstance(adapter_cfg, dict):
        return None, None
    atype = adapter_cfg.get("type")
    if not atype:
        return None, None

    from .agency.adapters import (
        AnthropicAdapter,
        GeminiAdapter,
        LMStudioAdapter,
        OllamaAdapter,
        OpenAIAdapter,
        OpenAICompatAdapter,
    )

    model = adapter_cfg.get("model") or fallback_model
    env_name = adapter_cfg.get("api_key_env")
    api_key = adapter_cfg.get("api_key", "") or (resolve_api_key(env_name) if env_name else "")
    base_url = adapter_cfg.get("base_url")

    if atype == "lmstudio":
        return LMStudioAdapter(model=model,
                                base_url=base_url or "http://localhost:1234/v1"), atype
    if atype == "ollama":
        return OllamaAdapter(model=model,
                              base_url=base_url or "http://localhost:11434"), atype
    if atype == "openai":
        return OpenAIAdapter(model=model,
                              base_url=base_url or "https://api.openai.com/v1",
                              api_key=api_key), atype
    if atype == "anthropic":
        return AnthropicAdapter(model=model,
                                 base_url=base_url or "https://api.anthropic.com",
                                 api_key=api_key), atype
    if atype == "gemini":
        return GeminiAdapter(model=model,
                              base_url=base_url or "https://generativelanguage.googleapis.com",
                              api_key=api_key), atype
    if atype == "openai-compat":
        return OpenAICompatAdapter(model=model,
                                    base_url=base_url or "http://localhost:8000/v1",
                                    api_key=api_key), atype
    if atype == "multi-fallback":
        from .agency.fallback_multi import MultiProviderFallbackAdapter
        providers_raw = adapter_cfg.get("providers", [])
        if not providers_raw or not isinstance(providers_raw, list):
            log.warning("multi-fallback requires 'providers' list in config")
            return None, None
        # Copy provider dicts so we don't mutate caller's config
        copied_providers = []
        for p in providers_raw:
            if isinstance(p, dict):
                p_copy = dict(p)
                if not p_copy.get("api_key") and api_key:
                    p_copy["api_key"] = api_key
                copied_providers.append(p_copy)
            else:
                copied_providers.append(p)
        return MultiProviderFallbackAdapter(
            providers=copied_providers,
            retry_per_provider=adapter_cfg.get("retry_per_provider", 2),
            backoff_base=adapter_cfg.get("backoff_base", 1.0),
            backoff_max=adapter_cfg.get("backoff_max", 10.0),
            timeout=adapter_cfg.get("timeout", 120.0),
        ), atype
    log.warning("unknown adapter type %r in config; ignoring", atype)
    return None, None
