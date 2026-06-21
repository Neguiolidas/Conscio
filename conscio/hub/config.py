"""Hub config layer: load / validate / redact / atomic save.

Reuses conscio.adapter_config for the canonical config paths + loader. Writes
are atomic (guards.atomic_write_text). Secrets are NEVER returned raw."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import adapter_config
from ..guards import atomic_write_text

KNOWN_TYPES = ("lmstudio", "ollama", "openai", "anthropic", "gemini",
               "openai-compat")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def load() -> dict:
    """Current config (or {} if none/corrupt)."""
    return adapter_config.load_config()


def config_path() -> Path:
    """First existing config path, else the first default (for first write)."""
    for p in adapter_config._CONFIG_PATHS:
        if p.exists():
            return p
    return adapter_config._CONFIG_PATHS[0]


def _valid_env_name(v) -> bool:
    return isinstance(v, str) and len(v) <= 128 and bool(_ENV_RE.match(v))


def _check_adapter(block: dict, where: str) -> list[str]:
    errs: list[str] = []
    atype = block.get("type")
    if atype not in KNOWN_TYPES:
        errs.append(f"{where}.type must be one of {KNOWN_TYPES}, got {atype!r}")
    bu = block.get("base_url")
    if bu is not None and not isinstance(bu, str):
        errs.append(f"{where}.base_url must be a string")
    env = block.get("api_key_env")
    if env is not None and not _valid_env_name(env):
        errs.append(f"{where}.api_key_env must be an ENV VAR NAME "
                    f"(^[A-Z_][A-Z0-9_]*$, <=128), not a key")
    return errs


def validate(cfg: dict) -> list[str]:
    """Return human-readable errors ([] = valid). Enforces the security
    contract: api_key_env is a NAME, never a raw key."""
    errs: list[str] = []
    model = cfg.get("model")
    if not isinstance(model, str) or not model.strip():
        errs.append("model must be a non-empty string")
    adapter = cfg.get("adapter")
    if not isinstance(adapter, dict):
        errs.append("adapter must be an object")
    else:
        errs += _check_adapter(adapter, "adapter")
    providers = cfg.get("providers", {})
    if providers:
        if not isinstance(providers, dict):
            errs.append("providers must be an object")
        else:
            for name, block in providers.items():
                if not isinstance(block, dict):
                    errs.append(f"providers.{name} must be an object")
                    continue
                errs += _check_adapter(block, f"providers.{name}")
    return errs
