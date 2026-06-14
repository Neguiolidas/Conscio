"""
Model Registry — Maps model identifiers to context window sizes and capabilities.

This is the foundation of context-awareness: the framework MUST know
what model it's running on and how much context it has available.
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


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
    has_json_mode: bool = False   # backend reliably honors JSON output mode
    supports_gbnf: bool = False   # backend supports GBNF grammar constraint

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
    def query_context_from_endpoint(cls, base_url: str,
                                    model_name: str) -> Optional[int]:
        """Query an OpenAI-compatible /v1/models endpoint for context_length.

        Returns the context_length if found, None otherwise.
        Works with LM Studio, vLLM, llama.cpp server, and any endpoint
        that returns context_length in the model metadata.
        """
        url = base_url.rstrip("/")
        # Try /v1/models first (OpenAI format)
        models_url = f"{url}/models"
        if not models_url.startswith("http"):
            models_url = f"http://{models_url}"

        try:
            req = urllib.request.Request(
                models_url,
                headers={"Content-Type": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Search for the model in the response
            for model in data.get("data", []):
                if model.get("id") == model_name:
                    ctx = model.get("context_length")
                    if ctx is not None and isinstance(ctx, (int, float)) and ctx > 0:
                        return int(ctx)
            return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Endpoint probe failed for {models_url}: {e}")
            return None

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
    def detect(cls, model_name: str, context_window: Optional[int] = None,
               base_url: Optional[str] = None) -> ModelInfo:
        """
        Detect model info with auto-detection from endpoint.

        Resolution order:
        1. Known model in registry (world recognition: strengths, notes)
        2. Explicit context_window override
        3. Endpoint probe (GET /v1/models → context_length)
        4. Heuristic from model name
        5. Default 128k fallback

        The _known_models table is world recognition only — it describes
        capabilities (strengths, notes) but context_window is always
        overridden by endpoint data when available.
        """
        info = cls.lookup(model_name)

        # Explicit override takes priority
        if context_window is not None:
            mode = cls.detect_mode(context_window)
            if info is not None:
                return ModelInfo(
                    name=model_name,
                    context_window=context_window,
                    mode=mode,
                    strengths=info.strengths,
                    notes=f"Context window overridden to {context_window}. Original: {info.context_window}.",
                )
            return ModelInfo(
                name=model_name,
                context_window=context_window,
                mode=mode,
                notes="Context window provided by user.",
            )

        # Try endpoint probe if base_url provided
        if base_url:
            endpoint_ctx = cls.query_context_from_endpoint(base_url, model_name)
            if endpoint_ctx is not None:
                mode = cls.detect_mode(endpoint_ctx)
                strengths = info.strengths if info else []
                notes = f"Context window auto-detected from endpoint: {endpoint_ctx}."
                if info:
                    notes += f" Original registry: {info.context_window}."
                return ModelInfo(
                    name=model_name,
                    context_window=endpoint_ctx,
                    mode=mode,
                    strengths=strengths,
                    notes=notes,
                )

        # Known model (world recognition)
        if info is not None:
            return info

        # Heuristic from name
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
