"""Single source of truth for provider specs (v4.9, Jade catalog item 7).

Every surface that needs a provider's default base URL, key env or probe
reads conscio/model-providers.json through this cached loader. The hub's
known_types() (item 10) already derives its tuple from the same file;
the adapter builder keeps its hardcoded builders (they construct classes,
not URLs) but takes the *defaults* from here, so a provider move is a
one-line JSON edit instead of a code chase.

Fail-fast: the JSON is static, shipped with the package, and validated on
every field the consumers use — a malformed file is a packaging bug, and
silently falling back would hide it. The error names the file and the
field so the fix is obvious.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("conscio.provider_specs")

_JSON_PATH = Path(__file__).resolve().parent / "model-providers.json"

_REQUIRED_FIELDS = ("base_url", "api_key_env", "probe", "models_path")
_KNOWN_FALLBACKS = frozenset({
    "lmstudio", "ollama", "openai", "anthropic", "gemini", "openai-compat",
})


@lru_cache(maxsize=1)
def load_provider_specs() -> dict[str, dict[str, Any]]:
    """Load and validate the provider spec table. Raises on malformed JSON.

    One lru_cache entry: the file is static per install, so the disk is
    read once per process. A parse failure raises ValueError (fail-fast)
    instead of returning a partial table that would make callers probe
    with wrong defaults.
    """
    try:
        data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"provider spec file unreadable: {_JSON_PATH}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"provider spec file is not valid JSON: {_JSON_PATH}: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ValueError(
            f"provider spec file must be a non-empty object: {_JSON_PATH}")
    for name, spec in data.items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"provider {name!r} must map to an object in {_JSON_PATH}")
        missing = [f for f in _REQUIRED_FIELDS if f not in spec]
        if missing:
            raise ValueError(
                f"provider {name!r} is missing field(s) {missing} in {_JSON_PATH}")
        if not isinstance(spec["base_url"], str) or not spec["base_url"]:
            raise ValueError(
                f"provider {name!r} has an empty base_url in {_JSON_PATH}")
    log.debug("provider specs loaded from %s (%d providers)", _JSON_PATH, len(data))
    return data


def provider_types() -> tuple[str, ...]:
    """The provider type names the JSON declares (single source of truth)."""
    return tuple(sorted(load_provider_specs().keys()))


def default_base_url(provider_type: str) -> str:
    """The shipped default base URL for a provider type.

    Raises KeyError for an unknown type: callers validate against
    provider_types() first, so a KeyError here is a real bug, not a
    fallback case.
    """
    return str(load_provider_specs()[provider_type]["base_url"])
