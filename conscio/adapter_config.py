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
        AnthropicAdapter, GeminiAdapter, LMStudioAdapter,
        OllamaAdapter, OpenAIAdapter, OpenAICompatAdapter,
    )

    model = adapter_cfg.get("model") or fallback_model
    api_key = adapter_cfg.get("api_key", "")
    if not api_key:
        env_name = adapter_cfg.get("api_key_env")
        if env_name:
            api_key = os.environ.get(env_name, "")
            if not api_key:
                # The Hub stores keys in its vault file, not the environment.
                # Fall back to it so the daemon/MCP/CLI don't build a keyless
                # adapter (-> 401). Lazy import: hub.config imports this module.
                try:
                    from .hub.config import vault_load
                    api_key = vault_load(env_name) or ""
                except Exception:               # hub optional; never break build
                    api_key = ""
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
    log.warning("unknown adapter type %r in config; ignoring", atype)
    return None, None
