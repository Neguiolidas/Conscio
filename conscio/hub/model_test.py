"""One-shot provider smoke test: build the adapter, call generate once, time it.
Never raises — failures come back as {"ok": False, "error": ...}. The API key is
resolved from the environment in-process and never returned."""
from __future__ import annotations

import os
import time

from ..adapter_config import build_adapter_from_config
from ..agency.adapter import AdapterError


def _build(provider_cfg: dict, model: str):
    # Ensure vault keys are loaded into os.environ before building adapter
    env = provider_cfg.get("api_key_env")
    if env and not os.environ.get(env):
        from . import config as _cfg
        _cfg.vault_load(env)
    adapter, _ = build_adapter_from_config(
        {"adapter": {**provider_cfg, "model": model}}, fallback_model=model)
    return adapter


def smoke_test(provider_cfg: dict, model: str,
               prompt: str = "Reply with OK.") -> dict:
    adapter = _build(provider_cfg, model)
    if adapter is None:
        return {"ok": False, "model": model, "latency_ms": 0,
                "sample_output": "", "error": "no adapter for this provider type"}
    start = time.monotonic()
    try:
        result = adapter.generate(prompt, max_tokens=16, temperature=0)
    except AdapterError as exc:
        return {"ok": False, "model": model, "latency_ms": 0,
                "sample_output": "", "error": str(exc)}
    except Exception as exc:                       # never leak a traceback
        return {"ok": False, "model": model, "latency_ms": 0,
                "sample_output": "", "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "model": model,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "sample_output": result.text[:200]}
