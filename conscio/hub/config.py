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
