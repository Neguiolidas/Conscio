"""Hub config layer: load / validate / redact / atomic save.

Reuses conscio.adapter_config for the canonical config paths + loader. Writes
are atomic (guards.atomic_write_text). Secrets are NEVER returned raw."""
from __future__ import annotations

import copy
import json
import os
import re
import urllib.parse
from pathlib import Path

from .. import adapter_config
from ..guards import atomic_write_text

KNOWN_TYPES = ("lmstudio", "ollama", "openai", "anthropic", "gemini",
               "openai-compat")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# ── Key vault: stores raw API keys in ~/.config/conscio/keys/ ──────
# (env-name safety uses _valid_env_name, defined below alongside _check_adapter)
_VAULT_DIR = adapter_config._CONFIG_PATHS[0].parent / "keys"


def _env_name_for(provider_type: str, model: str = "") -> str:
    """Stable, path-safe env var name for a provider type + optional model.

    Both inputs are sanitised to ``[a-z0-9]`` runs so a hostile ``type`` or
    ``model`` can never inject path separators into the vault filename (I1)."""
    base = re.sub(r"[^a-z0-9]", "_", provider_type.lower())
    if model:
        slug = re.sub(r"[^a-z0-9]", "_", model.lower())[:24]
        base += f"_{slug}"
    return f"CONSCIO_KEY_{base.upper()}"


def vault_store(env_name: str, raw_key: str) -> None:
    """Store a raw API key in the vault. Refuses unsafe names (I1).

    The file is created 0600 *before* any content is written (I2 — no
    chmod-after-rename window) and the vault dir is 0700 (M1)."""
    if not _valid_env_name(env_name):
        raise ValueError(f"invalid vault key name: {env_name!r}")
    _VAULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_VAULT_DIR, 0o700)
    path = _VAULT_DIR / env_name
    val = raw_key.strip()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)            # robust even if file pre-existed
        os.write(fd, val.encode())
    finally:
        os.close(fd)
    os.environ[env_name] = val          # so model_test works immediately


def vault_load(env_name: str) -> str | None:
    """Load a raw API key from env (preferred) or vault. None on bad name."""
    if not _valid_env_name(env_name):
        return None
    cur = os.environ.get(env_name)
    if cur:
        return cur
    try:
        val = (_VAULT_DIR / env_name).read_text().strip()
    except OSError:
        return None
    if val:
        os.environ[env_name] = val      # cache in env
        return val
    return None


def vault_has(env_name: str) -> bool:
    """True if a key exists in env or vault — WITHOUT mutating os.environ (M2)."""
    if not _valid_env_name(env_name):
        return False
    if os.environ.get(env_name):
        return True
    return (_VAULT_DIR / env_name).is_file()


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


def _check_base_url(bu: str, where: str) -> list[str]:
    """Scheme/host allowlist: http(s) only, host required, no embedded creds.
    Blocks file:// reads and credential-in-URL before probe_models urlopens it."""
    parsed = urllib.parse.urlparse(bu)
    if parsed.scheme not in ("http", "https"):
        return [f"{where}.base_url must use http or https, got {parsed.scheme!r}"]
    if not parsed.hostname:
        return [f"{where}.base_url must include a host"]
    if parsed.username or parsed.password:
        return [f"{where}.base_url must not embed credentials"]
    return []


def _check_adapter(block: dict, where: str) -> list[str]:
    errs: list[str] = []
    atype = block.get("type")
    if atype not in KNOWN_TYPES:
        errs.append(f"{where}.type must be one of {KNOWN_TYPES}, got {atype!r}")
    bu = block.get("base_url")
    if bu is not None:
        if not isinstance(bu, str):
            errs.append(f"{where}.base_url must be a string")
        else:
            errs += _check_base_url(bu, where)
    env = block.get("api_key_env")
    if env is not None and not _valid_env_name(env):
        errs.append(f"{where}.api_key_env must be an ENV VAR NAME "
                    f"(^[A-Z_][A-Z0-9_]*$, <=128), not a key")
    if "api_key" in block:
        errs.append(f"{where}.api_key is not allowed in config; "
                    f"use api_key_env (the NAME of an environment variable) "
                    f"— raw keys go through the vault on save")
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


def _redact_block(block: dict) -> dict:
    out = dict(block)
    out.pop("api_key", None)                       # never echo a raw key
    env = out.get("api_key_env")
    if env is not None:
        out["api_key_present"] = vault_has(env)    # M2: no os.environ mutation
    return out


def redact(cfg: dict) -> dict:
    """Deep copy safe to return over the API: raw api_key dropped everywhere,
    api_key_env annotated with api_key_present."""
    out = copy.deepcopy(cfg)
    if isinstance(cfg.get("adapter"), dict):
        out["adapter"] = _redact_block(cfg["adapter"])
    if isinstance(cfg.get("providers"), dict):
        out["providers"] = {k: _redact_block(v) if isinstance(v, dict) else v
                            for k, v in cfg["providers"].items()}
    return out


def save(cfg: dict) -> None:
    """Validate then atomically persist. Raises ValueError if invalid (no write)."""
    errs = validate(cfg)
    if errs:
        raise ValueError("; ".join(errs))
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # config stores api_key_env (env var NAMES), never raw secrets, so the brief
    # window between os.replace and chmod exposes no credentials.
    atomic_write_text(path, json.dumps(cfg, indent=2))
    os.chmod(path, 0o600)
