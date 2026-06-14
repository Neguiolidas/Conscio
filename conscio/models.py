"""
Model Registry — Maps model identifiers to context window sizes and capabilities.

This is the foundation of context-awareness: the framework MUST know
what model it's running on and how much context it has available.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError


class ContextMode(Enum):
    """Operating mode based on available context."""
    MINIMAL = "minimal"     # < 128k tokens
    COMPACT = "compact"     # 128k–256k tokens
    STANDARD = "standard"   # 256k+ tokens


@dataclass
class ModelInfo:
    """Information about a specific AI model."""
    name: str
    context_window: int
    mode: ContextMode
    strengths: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def available_context_tokens(self) -> int:
        """Effective context after overhead (system prompt, tools, etc.)."""
        # Reserve ~20% for system prompt, tools, conversation overhead
        return int(self.context_window * 0.8)

    def context_for_consciousness(self) -> int:
        """How many tokens the consciousness state is allowed to use."""
        from .context_manager import CONTEXT_BUDGET_PCT
        return int(self.available_context_tokens * CONTEXT_BUDGET_PCT)


class ModelRegistry:
    """
    Registry of known models and their context windows.
    
    Can load from the YAML config or be populated programmatically.
    Falls back to heuristic detection if the model isn't in the registry.
    """

    # Runtime-populated registry from autodiscover() or manual registration.
    _world_registry: dict[str, int] = {}

    # Known models with their context windows
    _known_models: dict[str, ModelInfo] = {
        # Primary
        "glm-5.1": ModelInfo("glm-5.1", 131_000, ContextMode.COMPACT,
                             ["complex_reasoning", "coding", "analysis"],
                             "Superior in complex tasks. 131k ctx baseline constraint."),
        "glm-5": ModelInfo("glm-5", 131_000, ContextMode.COMPACT,
                           ["complex_reasoning", "coding", "analysis"]),
        "kimi-k2.6": ModelInfo("kimi-k2.6", 256_000, ContextMode.STANDARD,
                               ["reasoning", "long_context"],
                               "Rate-limited by context usage. Use for delegation."),
        "kimi-k2": ModelInfo("kimi-k2", 256_000, ContextMode.STANDARD,
                             ["reasoning", "long_context"]),
        "minimax-m2.7": ModelInfo("minimax-m2.7", 260_000, ContextMode.STANDARD,
                                  ["long_context"],
                                  "Large ctx but weaker on complex tasks."),
        "step-flash-3.7": ModelInfo("step-flash-3.7", 260_000, ContextMode.STANDARD,
                                    ["speed", "long_context"],
                                    "Fast but inconsistent on complex tasks."),
        "deepseek-v4-pro": ModelInfo("deepseek-v4-pro", 128_000, ContextMode.COMPACT,
                                     ["reasoning"],
                                     "UNSTABLE — NVIDIA build shared. Not reliable."),
        "deepseek-v4-flash": ModelInfo("deepseek-v4-flash", 128_000, ContextMode.COMPACT,
                                       ["speed"],
                                       "UNSTABLE — same NVIDIA build issue."),
        "nemotron-3-super-120b": ModelInfo("nemotron-3-super-120b", 1_000_000,
                                           ContextMode.STANDARD,
                                           ["long_context"],
                                           "1M ctx but HALLUCINATES easily. Use with caution."),
        # Premium
        "claude-sonnet-4": ModelInfo("claude-sonnet-4", 200_000, ContextMode.STANDARD,
                                     ["complex_reasoning", "coding", "analysis"]),
        "claude-opus-4": ModelInfo("claude-opus-4", 200_000, ContextMode.STANDARD,
                                   ["complex_reasoning", "deep_analysis"],
                                   "Most capable but expensive."),
        "gpt-4o": ModelInfo("gpt-4o", 128_000, ContextMode.COMPACT,
                            ["general", "multimodal"]),
        # Open Source
        "llama-3.1-70b": ModelInfo("llama-3.1-70b", 128_000, ContextMode.COMPACT,
                                   ["general"]),
        "qwen-2.5-72b": ModelInfo("qwen-2.5-72b", 131_000, ContextMode.COMPACT,
                                  ["multilingual", "reasoning"]),
        "mistral-large": ModelInfo("mistral-large", 128_000, ContextMode.COMPACT,
                                   ["reasoning", "coding"]),
    }

    # Aliases — common shorthand names
    _aliases: dict[str, str] = {
        "glm": "glm-5.1",
        "glm5": "glm-5.1",
        "kimi": "kimi-k2.6",
        "minimax": "minimax-m2.7",
        "step": "step-flash-3.7",
        "step-flash": "step-flash-3.7",
        "deepseek": "deepseek-v4-pro",
        "nemotron": "nemotron-3-super-120b",
        "claude": "claude-sonnet-4",
        "sonnet": "claude-sonnet-4",
        "opus": "claude-opus-4",
        "gpt4": "gpt-4o",
    }

    # Context window thresholds
    MINIMAL_THRESHOLD = 128_000
    COMPACT_THRESHOLD = 256_000

    @classmethod
    def detect_mode(cls, context_window: int) -> ContextMode:
        """Determine operating mode from context window size."""
        if context_window < cls.MINIMAL_THRESHOLD:
            return ContextMode.MINIMAL
        elif context_window < cls.COMPACT_THRESHOLD:
            return ContextMode.COMPACT
        else:
            return ContextMode.STANDARD

    @classmethod
    def lookup(cls, model_name: str) -> Optional[ModelInfo]:
        """
        Look up a model by name or alias.
        
        Tries exact match first, then alias, then fuzzy match.
        Returns None if the model is completely unknown.
        """
        # Normalize
        key = model_name.lower().strip()

        # Exact match
        if key in cls._known_models:
            return cls._known_models[key]

        # Alias match
        if key in cls._aliases:
            return cls._known_models[cls._aliases[key]]

        # Fuzzy: check if any known model name contains the query
        for name, info in cls._known_models.items():
            if key in name or name in key:
                return info

        return None

    @classmethod
    def detect(cls, model_name: str, context_window: Optional[int] = None) -> ModelInfo:
        """
        Detect model info, falling back to heuristic if unknown.
        
        Priority order:
        1. Explicit context_window argument (user override)
        2. CONSCIO_CONTEXT_WINDOW environment variable
        3. Known model registry (hardcoded)
        4. World registry (autodiscovered from endpoints)
        5. Heuristic extraction from model name
        6. Default 128k
        """
        # 1. Env var override (highest priority after explicit arg)
        env_ctx = os.environ.get("CONSCIO_CONTEXT_WINDOW")
        if env_ctx and context_window is None:
            try:
                context_window = int(env_ctx)
            except ValueError:
                pass

        info = cls.lookup(model_name)
        if info is not None:
            if context_window is not None and context_window != info.context_window:
                # User/env override — trust it
                mode = cls.detect_mode(context_window)
                return ModelInfo(
                    name=model_name,
                    context_window=context_window,
                    mode=mode,
                    strengths=info.strengths,
                    notes=f"Context window overridden to {context_window}. Original: {info.context_window}.",
                )
            return info

        # 2. Explicit context_window (user or env)
        if context_window is not None:
            mode = cls.detect_mode(context_window)
            return ModelInfo(
                name=model_name,
                context_window=context_window,
                mode=mode,
                notes="Unknown model — context window provided by user.",
            )

        # 3. World registry (autodiscovered from local endpoints)
        world_ctx = cls._world_registry.get(model_name.lower())
        if world_ctx is not None:
            mode = cls.detect_mode(world_ctx)
            return ModelInfo(
                name=model_name,
                context_window=world_ctx,
                mode=mode,
                notes=f"Context window {world_ctx} from world_registry (autodiscovered).",
            )

        # 4. Last resort: try to extract from name (e.g., "model-128k")
        ctx = cls._extract_context_from_name(model_name)
        mode = cls.detect_mode(ctx)
        return ModelInfo(
            name=model_name,
            context_window=ctx,
            mode=mode,
            notes=f"Unknown model — context window inferred as {ctx} from name.",
        )

    @classmethod
    def _extract_context_from_name(cls, name: str) -> int:
        """Try to extract context window size from model name."""
        # Look for patterns like "128k", "256k", "1m"
        match = re.search(r'(\d+)([km])', name.lower())
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            if unit == 'k':
                return num * 1_000
            elif unit == 'm':
                return num * 1_000_000

        # Default: assume compact (128k) for safety
        return 128_000

    @classmethod
    def register(cls, name: str, context_window: int,
                 strengths: Optional[list[str]] = None,
                 notes: str = "") -> ModelInfo:
        """Register a new model in the registry."""
        mode = cls.detect_mode(context_window)
        info = ModelInfo(
            name=name,
            context_window=context_window,
            mode=mode,
            strengths=strengths or [],
            notes=notes,
        )
        cls._known_models[name.lower()] = info
        return info

    @classmethod
    def all_models(cls) -> dict[str, ModelInfo]:
        """Return all registered models."""
        return dict(cls._known_models)

    # --- World registry & autodiscovery ---

    @classmethod
    def get_world_context(cls, model_name: str) -> Optional[int]:
        """Return autodiscovered context window for a model, or None."""
        return cls._world_registry.get(model_name.lower())

    @classmethod
    def autodiscover(cls, timeout: float = 2.0) -> int:
        """
        Probe local inference endpoints and register models + context windows.

        Probes in order:
        1. CONSCIO_ENDPOINTS env var (comma-separated URLs)
        2. LM Studio (http://localhost:1234)
        3. Ollama (http://localhost:11434)

        Returns the number of models registered.
        Safe to call at startup — failures are silently ignored.
        """
        import logging
        log = logging.getLogger(__name__)

        # 1. Env override
        env_endpoints = os.environ.get("CONSCIO_ENDPOINTS", "")
        if env_endpoints:
            for ep in env_endpoints.split(","):
                ep = ep.strip()
                if ep:
                    try:
                        found = cls._probe_openai_endpoint(ep, timeout=timeout)
                        for name, ctx in found.items():
                            cls._world_registry[name.lower()] = ctx
                        if found:
                            log.info("autodiscover %s: %d models", ep, len(found))
                    except Exception:
                        log.debug("autodiscover %s failed", ep, exc_info=True)

        # 2. LM Studio
        try:
            found = cls._probe_lmstudio(timeout=timeout)
            for name, ctx in found.items():
                cls._world_registry[name.lower()] = ctx
            if found:
                log.info("autodiscover LM Studio: %d models", len(found))
        except Exception:
            log.debug("autodiscover LM Studio failed", exc_info=True)

        # 3. Ollama
        try:
            found = cls._probe_ollama(timeout=timeout)
            for name, ctx in found.items():
                cls._world_registry[name.lower()] = ctx
            if found:
                log.info("autodiscover Ollama: %d models", len(found))
        except Exception:
            log.debug("autodiscover Ollama failed", exc_info=True)

        return len(cls._world_registry)

    @classmethod
    def _probe_lmstudio(cls, timeout: float = 2.0) -> dict[str, int]:
        """Probe LM Studio for loaded models and their context windows."""
        base = os.environ.get("LM_STUDIO_URL", "http://localhost:1234")
        result: dict[str, int] = {}

        # List models
        req = Request(f"{base}/v1/models", method="GET")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())

        models = data.get("data", [])
        if not models:
            return result

        # Try to get context from state endpoint
        state_ctx = cls._query_lmstudio_state(base, timeout=timeout)

        for m in models:
            name = m.get("id", "")
            if not name:
                continue
            # Use state context if available, else try model metadata
            ctx = state_ctx
            if ctx is None:
                # Try extracting from model object
                ctx = m.get("context_length") or m.get("max_context_length")
            if ctx is not None:
                result[name] = int(ctx)

        return result

    @classmethod
    def _query_lmstudio_state(cls, base: str, timeout: float = 2.0) -> Optional[int]:
        """Query LM Studio state for the loaded model's context length."""
        try:
            req = Request(f"{base}/v1/state", method="GET")
            with urlopen(req, timeout=timeout) as resp:
                state = json.loads(resp.read())
            # Try nested paths: llm.load.contextLength
            llm = state.get("llm", {})
            load = llm.get("load", {})
            ctx = load.get("contextLength")
            if ctx is not None:
                return int(ctx)
        except Exception:
            pass
        return None

    @classmethod
    def _probe_ollama(cls, timeout: float = 2.0) -> dict[str, int]:
        """Probe Ollama for available models and their context windows."""
        import logging
        log = logging.getLogger(__name__)
        base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        result: dict[str, int] = {}

        # List models via /api/tags
        try:
            req = Request(f"{base}/api/tags", method="GET")
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except URLError:
            return result

        models = data.get("models", [])
        for m in models:
            name = m.get("name", "")
            if not name:
                continue
            # Try /api/show for each model to get context length
            try:
                show_req = Request(
                    f"{base}/api/show",
                    data=json.dumps({"name": name}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(show_req, timeout=timeout) as resp:
                    details = json.loads(resp.read())
                ctx = details.get("details", {}).get("context_length")
                if ctx is not None:
                    result[name] = int(ctx)
            except Exception:
                log.debug("Failed to get context for Ollama model %s", name, exc_info=True)

        return result

    @classmethod
    def _ollama_list(cls, base: str, timeout: float = 2.0) -> list[str]:
        """List model names from Ollama."""
        req = Request(f"{base}/api/tags", method="GET")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    @classmethod
    def _probe_openai_endpoint(cls, base_url: str, timeout: float = 2.0) -> dict[str, int]:
        """Probe an OpenAI-compatible /v1/models endpoint for context lengths."""
        url = base_url.rstrip("/")
        if not url.endswith("/v1/models"):
            url = f"{url}/v1/models"

        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())

        result: dict[str, int] = {}
        for m in data.get("data", []):
            name = m.get("id", "")
            ctx = m.get("context_length") or m.get("max_context_length")
            if name and ctx is not None:
                result[name] = int(ctx)
        return result

    @classmethod
    def write_default_config(cls, config_path: Optional[str | Path] = None) -> Path:
        """
        Write a default conscio config file with all known context windows.

        Only writes if the file doesn't exist. Returns the path.
        """
        if config_path is None:
            config_path = Path.home() / ".config" / "conscio" / "config.yaml"
        config_path = Path(config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            return config_path

        # Build a minimal YAML-like config (no yaml dep needed)
        lines = ["# Conscio auto-generated config", "# context_window: override in tokens"]
        # Pick the most recently autodiscovered or a reasonable default
        if cls._world_registry:
            # Use the first discovered model as example
            example_name, example_ctx = next(iter(cls._world_registry.items()))
            lines.append(f"# Example: {example_name} = {example_ctx}")
        lines.append(f"context_window: 128000  # default fallback")
        config_path.write_text("\n".join(lines) + "\n")
        return config_path
