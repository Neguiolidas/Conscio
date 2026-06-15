"""
Model Registry — Maps model identifiers to context window sizes and capabilities.

This is the foundation of context-awareness: the framework MUST know
what model it's running on and how much context it has available.
"""

from __future__ import annotations

import json
import os
import re
import struct
import urllib.request
import urllib.error
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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

    # Config file path for persistent model context overrides
    _CONFIG_PATHS = [
        Path.home() / ".config" / "conscio" / "config.yaml",
        Path.home() / ".conscio" / "config.yaml",
    ]

    @classmethod
    def _read_config_context(cls, model_name: str) -> Optional[int]:
        """Read context_window from conscio config file.

        Config format (YAML):
            models:
              mimo-v2.5-pro:
                context_window: 1048576
              qwen3.5-0.8b:
                context_window: 32000
        """
        for config_path in cls._CONFIG_PATHS:
            if not config_path.exists():
                continue
            try:
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
                models = config.get("models", {})
                if not isinstance(models, dict):
                    continue
                model_cfg = models.get(model_name)
                if isinstance(model_cfg, dict):
                    ctx = model_cfg.get("context_window")
                    if isinstance(ctx, (int, float)) and ctx > 0:
                        return int(ctx)
                # Also try flat format: context_window: {model: ctx}
                ctx_map = config.get("context_window")
                if isinstance(ctx_map, dict):
                    ctx = ctx_map.get(model_name)
                    if isinstance(ctx, (int, float)) and ctx > 0:
                        return int(ctx)
            except Exception:
                continue
        return None

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

    # Directories where local GGUF models are commonly stored
    _GGUF_SEARCH_DIRS = [
        Path.home() / ".lmstudio" / "models",
        Path.home() / ".ollama" / "models",
        Path.home() / "models",
    ]

    @classmethod
    def query_context_from_lmstudio(cls, model_name: str) -> Optional[int]:
        """Read active context_length from LM Studio conversation state.

        LM Studio stores the loaded model's contextLength in its conversation
        JSON files under lastUsedModel.instanceLoadTimeConfig. This returns
        the ACTIVE context window (what the user configured), not the GGUF max.

        Returns None if LM Studio state is not found or doesn't match.
        """
        lmstudio_dir = Path.home() / ".lmstudio" / "conversations"
        if not lmstudio_dir.exists():
            return None

        model_norm = cls._normalize_model_name(model_name)
        best_ctx = None
        best_ts = 0

        try:
            for conv_file in sorted(lmstudio_dir.glob("*.conversation.json"),
                                    reverse=True):
                with open(conv_file) as f:
                    data = json.load(f)

                # Check if this conversation's model matches
                lum = data.get("lastUsedModel", {})
                identifier = lum.get("identifier", "")
                if model_norm not in cls._normalize_model_name(identifier):
                    continue

                # Extract contextLength from instanceLoadTimeConfig
                ilc = lum.get("instanceLoadTimeConfig", {})
                for field in ilc.get("fields", []):
                    if field.get("key") == "llm.load.contextLength":
                        ctx = field.get("value")
                        if isinstance(ctx, (int, float)) and ctx > 0:
                            # Use the most recent conversation's value
                            ts_str = conv_file.stem.split(".")[0]
                            try:
                                ts = int(ts_str)
                            except ValueError:
                                ts = 0
                            if ts > best_ts:
                                best_ts = ts
                                best_ctx = int(ctx)
                        break

                if best_ctx:
                    break  # Found in most recent file

        except Exception as e:
            logger.debug(f"LM Studio state read failed: {e}")
            return None

        if best_ctx:
            logger.debug(f"LM Studio active context for {model_name}: {best_ctx}")
        return best_ctx

    @classmethod
    def _read_gguf_context_length(cls, gguf_path: str) -> Optional[int]:
        """Read context_length from GGUF file metadata."""
        try:
            with open(gguf_path, "rb") as f:
                magic = f.read(4)
                if magic != b'GGUF':
                    return None
                _version = struct.unpack('<I', f.read(4))[0]
                _n_tensors = struct.unpack('<Q', f.read(8))[0]
                n_kv = struct.unpack('<Q', f.read(8))[0]

                # GGUF value type sizes
                _type_sizes = {
                    0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
                    10: 8, 11: 8, 12: 8,
                }

                for _ in range(min(n_kv, 500)):
                    key_len = struct.unpack('<Q', f.read(8))[0]
                    key = f.read(key_len).decode('utf-8', errors='replace')
                    val_type = struct.unpack('<I', f.read(4))[0]

                    if val_type == 4:  # UINT32
                        val = struct.unpack('<I', f.read(4))[0]
                    elif val_type == 10:  # UINT64
                        val = struct.unpack('<Q', f.read(8))[0]
                    elif val_type == 6:  # FLOAT32
                        val = struct.unpack('<f', f.read(4))[0]
                    elif val_type == 0:  # UINT8
                        val = struct.unpack('B', f.read(1))[0]
                    elif val_type == 1:  # INT8
                        val = struct.unpack('b', f.read(1))[0]
                    elif val_type == 2:  # UINT16
                        val = struct.unpack('<H', f.read(2))[0]
                    elif val_type == 3:  # INT16
                        val = struct.unpack('<h', f.read(2))[0]
                    elif val_type == 5:  # INT32
                        val = struct.unpack('<i', f.read(4))[0]
                    elif val_type == 7:  # BOOL
                        val = struct.unpack('?', f.read(1))[0]
                    elif val_type == 8:  # STRING
                        slen = struct.unpack('<Q', f.read(8))[0]
                        f.read(slen)  # skip string value
                        continue
                    elif val_type == 11:  # INT64
                        val = struct.unpack('<q', f.read(8))[0]
                    elif val_type == 12:  # FLOAT64
                        val = struct.unpack('<d', f.read(8))[0]
                    else:
                        return None  # Unknown type

                    if 'context_length' in key.lower():
                        return int(val)
        except Exception:
            return None
        return None

    @classmethod
    def _normalize_model_name(cls, name: str) -> str:
        """Normalize model name for fuzzy matching (strip non-alnum)."""
        return re.sub(r'[^a-z0-9]', '', name.lower())

    @classmethod
    def query_context_from_gguf(cls, model_name: str,
                                search_dirs: Optional[list] = None) -> Optional[int]:
        """Search local directories for a GGUF model and read its context_length.

        Works with LM Studio, Ollama, and any local GGUF model store.
        Returns the model's max context_length from GGUF metadata, or None.
        """
        dirs = search_dirs or cls._GGUF_SEARCH_DIRS
        model_norm = cls._normalize_model_name(model_name)

        for base_dir in dirs:
            if not base_dir.exists():
                continue
            for gguf in base_dir.rglob("*.gguf"):
                # Skip multimodal projector files
                if "mmproj" in gguf.name.lower():
                    continue
                file_norm = cls._normalize_model_name(gguf.stem)
                if model_norm in file_norm or file_norm.startswith(model_norm[:15]):
                    ctx = cls._read_gguf_context_length(str(gguf))
                    if ctx and ctx > 0:
                        logger.debug(f"GGUF context_length for {model_name}: {ctx} "
                                     f"(from {gguf})")
                        return ctx
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

    @staticmethod
    def _env_truthy(name: str) -> bool:
        """True if env var `name` is set to a truthy value (1/true/yes/on)."""
        return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def detect(cls, model_name: str, context_window: Optional[int] = None,
               base_url: Optional[str] = None,
               autodetect: bool = False) -> ModelInfo:
        """Resolve ModelInfo — offline & deterministic by default.

        The default path (no base_url, no autodetect) reads ONLY ``os.environ`` and
        the in-process registry — zero filesystem, zero network — so engine and
        context construction is host-independent and reproducible:

            1. explicit ``context_window`` argument     (programmatic override)
            2. env ``CONSCIO_CONTEXT_WINDOW``           (explicit, no I/O)
            3. endpoint probe of ``base_url``, if given (explicit, targeted network)
            4. [opt-in only] config.json -> LM Studio state -> GGUF scan,
               reachable via ``autodetect=True`` or env ``CONSCIO_AUTODETECT``
            5. known-model registry                     (curated truth)
            6. heuristic from the model name
            7. 128k fallback

        Ambient host-state reads (config file, LM Studio, GGUF) are gated behind the
        explicit opt-in. An explicit ``base_url`` enables only the probe of THAT
        endpoint, never a ``$HOME`` scan. GGUF reports a model's architectural MAX,
        so it is consulted last and labelled as such.
        """
        info = cls.lookup(model_name)

        # 1. Explicit override takes priority.
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

        # 2. Environment variable CONSCIO_CONTEXT_WINDOW (explicit, no I/O).
        env_ctx = os.environ.get("CONSCIO_CONTEXT_WINDOW")
        if env_ctx:
            try:
                ctx = int(env_ctx)
                if ctx > 0:
                    mode = cls.detect_mode(ctx)
                    strengths = info.strengths if info else []
                    notes = f"Context window from env CONSCIO_CONTEXT_WINDOW: {ctx}."
                    return ModelInfo(
                        name=model_name,
                        context_window=ctx,
                        mode=mode,
                        strengths=strengths,
                        notes=notes,
                    )
            except ValueError:
                pass

        # 3. Explicit, targeted endpoint probe — only the URL the caller named.
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

        # 4. OPT-IN ambient host-state path (autodetect=True or CONSCIO_AUTODETECT).
        #    These reads touch $HOME, so they are NEVER on the default path.
        if autodetect or cls._env_truthy("CONSCIO_AUTODETECT"):
            # 4a. Config file (explicit user value).
            config_ctx = cls._read_config_context(model_name)
            if config_ctx is not None:
                mode = cls.detect_mode(config_ctx)
                strengths = info.strengths if info else []
                notes = f"Context window from config file: {config_ctx}."
                return ModelInfo(
                    name=model_name,
                    context_window=config_ctx,
                    mode=mode,
                    strengths=strengths,
                    notes=notes,
                )

            # 4b. LM Studio active state (the loaded context — preferred over MAX).
            lmstudio_ctx = cls.query_context_from_lmstudio(model_name)
            if lmstudio_ctx is not None:
                mode = cls.detect_mode(lmstudio_ctx)
                strengths = info.strengths if info else []
                notes = f"Context window auto-detected from LM Studio state: {lmstudio_ctx}."
                if info:
                    notes += f" Original registry: {info.context_window}."
                return ModelInfo(
                    name=model_name,
                    context_window=lmstudio_ctx,
                    mode=mode,
                    strengths=strengths,
                    notes=notes,
                )

            # 4c. GGUF metadata scan — architectural MAX (may exceed active ctx).
            gguf_ctx = cls.query_context_from_gguf(model_name)
            if gguf_ctx is not None:
                mode = cls.detect_mode(gguf_ctx)
                strengths = info.strengths if info else []
                notes = (f"Context window from GGUF architectural max: {gguf_ctx} "
                         f"(may exceed the active/loaded context).")
                if info:
                    notes += f" Original registry: {info.context_window}."
                return ModelInfo(
                    name=model_name,
                    context_window=gguf_ctx,
                    mode=mode,
                    strengths=strengths,
                    notes=notes,
                )

        # 5. Known model (curated registry truth) — deterministic.
        if info is not None:
            return info

        # 6. Heuristic from name.
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
