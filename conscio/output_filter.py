"""
OutputFilter — 8-stage pipeline for compressing and filtering text.

Filters and compresses outputs before injecting into the agent's context
window or indexing into ContentStore. Each stage is independently
configurable, and the pipeline is crash-safe (falls back to original
text if any stage fails).

Inspired by rtk/src/core/toml_filter.rs — reimplemented in Python.
Declarative YAML config instead of TOML.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

# YAML is optional — fall back to manual config if unavailable
try:
    import yaml  # type: ignore[import-untyped]
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ─── Constants ──────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "consciousness" / "filters.yaml"

# ANSI escape code regex (covers most common sequences)
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[a-zA-Z]")


# ─── Stage Base ─────────────────────────────────────────────────────────

class FilterStage(ABC):
    """Base class for all filter stages."""

    @abstractmethod
    def apply(self, text: str) -> str:
        """Apply this filter stage to the text."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Return the stage name for logging."""
        ...


# ─── 8 Stages ───────────────────────────────────────────────────────────

class StripAnsi(FilterStage):
    """Stage 1: Remove ANSI escape codes."""

    def apply(self, text: str) -> str:
        return ANSI_PATTERN.sub("", text)

    def name(self) -> str:
        return "strip_ansi"


class Replace(FilterStage):
    """Stage 2: Regex substitutions."""

    def __init__(self, patterns: list[dict] | None = None):
        """
        Args:
            patterns: list of {"pattern": "...", "replacement": "..."} dicts
        """
        self.patterns = patterns or []
        self._compiled = [
            (re.compile(p["pattern"]), p.get("replacement", ""))
            for p in self.patterns
        ]

    def apply(self, text: str) -> str:
        for regex, replacement in self._compiled:
            text = regex.sub(replacement, text)
        return text

    def name(self) -> str:
        return "replace"


class MatchOutput(FilterStage):
    """Stage 3: Short-circuit — if text matches a rule, return the rule's message."""

    def __init__(self, rules: list[dict] | None = None):
        """
        Args:
            rules: list of {"pattern": "...", "message": "..."} dicts.
                   If the text matches the pattern, return the message instead.
        """
        self.rules = rules or []
        self._compiled = [
            (re.compile(r["pattern"]), r.get("message", "[matched]"))
            for r in self.rules
        ]

    def apply(self, text: str) -> str:
        for regex, message in self._compiled:
            if regex.search(text):
                return message
        return text

    def name(self) -> str:
        return "match_output"


class FilterLines(FilterStage):
    """Stage 4: Strip or keep lines by regex."""

    def __init__(self, mode: str = "strip", patterns: list[str] | None = None):
        """
        Args:
            mode: "strip" (remove matching) or "keep" (only keep matching)
            patterns: list of regex patterns
        """
        if mode not in ("strip", "keep"):
            raise ValueError(f"FilterLines mode must be 'strip' or 'keep', got '{mode}'")
        self.mode = mode
        self.patterns = [re.compile(p) for p in (patterns or [])]

    def apply(self, text: str) -> str:
        lines = text.split("\n")

        if self.mode == "strip":
            result = [
                line for line in lines
                if not any(p.search(line) for p in self.patterns)
            ]
        else:  # keep
            result = [
                line for line in lines
                if any(p.search(line) for p in self.patterns)
            ]

        return "\n".join(result)

    def name(self) -> str:
        return "filter_lines"


class TruncateLines(FilterStage):
    """Stage 5: Truncate long lines."""

    def __init__(self, max_width: int = 200, suffix: str = "..."):
        """
        Args:
            max_width: Maximum line length in characters
            suffix: Suffix to append to truncated lines
        """
        self.max_width = max(1, max_width)
        self.suffix = suffix

    def apply(self, text: str) -> str:
        lines = text.split("\n")
        result = []
        for line in lines:
            if len(line) > self.max_width:
                result.append(line[: self.max_width - len(self.suffix)] + self.suffix)
            else:
                result.append(line)
        return "\n".join(result)

    def name(self) -> str:
        return "truncate_lines"


class HeadTail(FilterStage):
    """Stage 6: Keep first N + last M lines."""

    def __init__(self, head: int = 50, tail: int = 20, separator: str = "..."):
        """
        Args:
            head: Number of lines to keep from the start
            tail: Number of lines to keep from the end
            separator: Separator between head and tail sections
        """
        self.head = max(0, head)
        self.tail = max(0, tail)
        self.separator = separator

    def apply(self, text: str) -> str:
        lines = text.split("\n")

        if len(lines) <= self.head + self.tail:
            return text

        head_lines = lines[: self.head]
        tail_lines = lines[-self.tail:] if self.tail > 0 else []

        parts = []
        if head_lines:
            parts.append("\n".join(head_lines))
        if self.separator:
            parts.append(self.separator)
        if tail_lines:
            parts.append("\n".join(tail_lines))

        return "\n".join(parts)

    def name(self) -> str:
        return "head_tail"


class MaxLines(FilterStage):
    """Stage 7: Absolute line cap."""

    def __init__(self, max_lines: int = 100):
        """
        Args:
            max_lines: Maximum number of lines to keep
        """
        self.max_lines = max(1, max_lines)

    def apply(self, text: str) -> str:
        lines = text.split("\n")
        if len(lines) <= self.max_lines:
            return text
        return "\n".join(lines[: self.max_lines])

    def name(self) -> str:
        return "max_lines"


class OnEmpty(FilterStage):
    """Stage 8: Fallback message when output is empty."""

    def __init__(self, message: str = "No relevant output"):
        """
        Args:
            message: Fallback message to return if text is empty
        """
        self.message = message

    def apply(self, text: str) -> str:
        return text if text.strip() else self.message

    def name(self) -> str:
        return "on_empty"


class DedupBlocks(FilterStage):
    """Stage: collapse consecutive identical lines into one + a count marker."""

    def __init__(self, min_run: int = 3, marker: str = "… (×{n})"):
        """
        Args:
            min_run: Minimum consecutive repeats before collapsing (>=2).
            marker: Template for the collapse marker; {n} = run length.
        """
        self.min_run = max(2, min_run)
        self.marker = marker

    def apply(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            j = i + 1
            while j < n and lines[j] == lines[i]:
                j += 1
            run = j - i
            out.append(lines[i])
            if run >= self.min_run:
                out.append(self.marker.format(n=run))
            elif run > 1:
                out.extend([lines[i]] * (run - 1))  # keep short runs verbatim
            i = j
        return "\n".join(out)

    def name(self) -> str:
        return "dedup_blocks"


class SemanticDedup(FilterStage):
    """Annotate semantically redundant ADJACENT blocks. NON-DESTRUCTIVE: never
    deletes or merges — appends a marker to the later block and keeps both
    verbatim. Offline (semantic unavailable / None) → returns text unchanged.

    Duck-typed `semantic`: any object with available() -> bool and
    cosine(a, b) -> float (e.g. conscio.semantic.SemanticEngine). embed() is
    cached there, so re-embedding adjacent blocks is free.

    Single-pass: meant to run once over fresh text inside FilterPipeline. Do NOT
    re-apply to already-annotated output — markers could stack.
    """

    def __init__(self, semantic=None, threshold: float = 0.88,
                 marker: str = " ↺ near-dup of above ({score:.2f})"):
        self.semantic = semantic
        self.threshold = threshold
        self.marker = marker

    def apply(self, text: str) -> str:
        if self.semantic is None or not self.semantic.available():
            return text
        blocks = text.split("\n\n")
        if len(blocks) < 2:
            return text
        out = [blocks[0]]
        for i in range(1, len(blocks)):
            score = self.semantic.cosine(blocks[i - 1], blocks[i])
            if score >= self.threshold:
                out.append(blocks[i] + self.marker.format(score=score))
            else:
                out.append(blocks[i])
        return "\n\n".join(out)

    def name(self) -> str:
        return "semantic_dedup"


# Secret shapes redacted whole; the key:value rule redacts only the value.
_SECRET_WHOLE = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
]
_SECRET_KEYVALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\b(\s*[:=]\s*)(\S+)"
)


class SecretMask(FilterStage):
    """Stage: redact common secret shapes (API keys, tokens, key:value pairs)."""

    def __init__(self, extra_patterns: list[str] | None = None,
                 replacement: str = "***REDACTED***"):
        """
        Args:
            extra_patterns: Additional whole-match regexes to redact.
            replacement: Replacement string for redacted secrets.
        """
        self.replacement = replacement
        self._whole = list(_SECRET_WHOLE) + [re.compile(p) for p in (extra_patterns or [])]

    def apply(self, text: str) -> str:
        for rx in self._whole:
            text = rx.sub(self.replacement, text)
        text = _SECRET_KEYVALUE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{self.replacement}", text
        )
        return text

    def name(self) -> str:
        return "secret_mask"


# ─── Stage Registry ─────────────────────────────────────────────────────

STAGE_REGISTRY: dict[str, type] = {
    "strip_ansi": StripAnsi,
    "replace": Replace,
    "match_output": MatchOutput,
    "filter_lines": FilterLines,
    "truncate_lines": TruncateLines,
    "head_tail": HeadTail,
    "max_lines": MaxLines,
    "on_empty": OnEmpty,
    "dedup_blocks": DedupBlocks,
    "semantic_dedup": SemanticDedup,
    "secret_mask": SecretMask,
}


def build_stage(name: str, config: dict | None = None) -> FilterStage:
    """Build a filter stage from name and config dict."""
    config = config or {}
    cls = STAGE_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown filter stage: '{name}'. Available: {list(STAGE_REGISTRY.keys())}")

    # Map config keys to constructor args for each stage
    if cls is StripAnsi or cls is OnEmpty:
        return cls(**{k: v for k, v in config.items() if k in ("message",)})
    elif cls is Replace:
        return cls(patterns=config.get("patterns", []))
    elif cls is MatchOutput:
        return cls(rules=config.get("rules", []))
    elif cls is FilterLines:
        return cls(mode=config.get("mode", "strip"), patterns=config.get("patterns", []))
    elif cls is TruncateLines:
        return cls(max_width=config.get("max_width", 200), suffix=config.get("suffix", "..."))
    elif cls is HeadTail:
        return cls(head=config.get("head", 50), tail=config.get("tail", 20),
                    separator=config.get("separator", "..."))
    elif cls is MaxLines:
        return cls(max_lines=config.get("max_lines", 100))
    elif cls is DedupBlocks:
        return cls(min_run=config.get("min_run", 3),
                   marker=config.get("marker", "… (×{n})"))
    elif cls is SemanticDedup:
        return cls(semantic=config.get("semantic"),
                   threshold=config.get("threshold", 0.88),
                   marker=config.get("marker", " ↺ near-dup of above ({score:.2f})"))
    elif cls is SecretMask:
        return cls(extra_patterns=config.get("extra_patterns"),
                   replacement=config.get("replacement", "***REDACTED***"))
    else:
        return cls()


# ─── FilterPipeline ─────────────────────────────────────────────────────

class FilterPipeline:
    """
    Multi-stage filter pipeline for text compression.

    Each stage is applied in order. If any stage raises an exception,
    the pipeline returns the text as it was before that stage (crash-safe).
    """

    def __init__(self, stages: list[FilterStage] | None = None):
        self.stages = stages or self._default_stages()

    def _default_stages(self) -> list[FilterStage]:
        """Build a sensible default pipeline."""
        return [
            StripAnsi(),
            Replace(),
            MatchOutput(),
            FilterLines(mode="strip", patterns=[]),
            TruncateLines(max_width=200),
            HeadTail(head=50, tail=20),
            MaxLines(max_lines=100),
            OnEmpty(),
        ]

    def apply(self, text: str) -> str:
        """
        Apply the full pipeline to text.

        Crash-safe: if any stage fails, returns the text as-is
        from the last successful stage.
        """
        result = text
        for stage in self.stages:
            try:
                result = stage.apply(result)
            except Exception:
                # Never break the workflow — return last good result
                return result
        return result

    def add_stage(self, stage: FilterStage, position: int | None = None) -> None:
        """Add a stage to the pipeline. Position -1 = append."""
        if position is None:
            self.stages.append(stage)
        else:
            self.stages.insert(position, stage)

    def remove_stage(self, name: str) -> bool:
        """Remove a stage by name. Returns True if found."""
        for i, s in enumerate(self.stages):
            if s.name() == name:
                self.stages.pop(i)
                return True
        return False

    def list_stages(self) -> list[str]:
        """Return ordered list of stage names."""
        return [s.name() for s in self.stages]


# ─── Pipeline Builder (from config) ─────────────────────────────────────

def build_pipeline_from_config(config_path: str | Path | None = None) -> FilterPipeline:
    """
    Build a FilterPipeline from a YAML config file.

    Config format:
    ```yaml
    filters:
      - name: default
        stages:
          - strip_ansi: {}
          - replace:
              patterns:
                - pattern: '\\d{4}-\\d{2}-\\d{2}'
                  replacement: '[DATE]'
          - filter_lines:
              mode: strip
              patterns: ['^DEBUG:', '^TRACE:']
          - max_lines: 30
    ```
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not path.exists() or not HAS_YAML:
        return FilterPipeline()  # Defaults

    with open(path) as f:
        config = yaml.safe_load(f)

    if not config or "filters" not in config:
        return FilterPipeline()

    # Use the first filter config as default
    filter_config = config["filters"][0] if config["filters"] else None
    if not filter_config:
        return FilterPipeline()

    stages = []
    for stage_config in filter_config.get("stages", []):
        # Each stage_config is a dict with one key (stage name)
        for stage_name, stage_params in stage_config.items():
            stages.append(build_stage(stage_name, stage_params or {}))

    return FilterPipeline(stages=stages) if stages else FilterPipeline()


def build_pipeline_from_dict(config: dict) -> FilterPipeline:
    """
    Build a FilterPipeline from a dict (programmatic config).

    Dict format:
    {
        "stages": [
            {"strip_ansi": {}},
            {"filter_lines": {"mode": "strip", "patterns": ["^DEBUG:"]}},
            {"max_lines": {"max_lines": 30}},
        ]
    }
    """
    stages = []
    for stage_config in config.get("stages", []):
        for stage_name, stage_params in stage_config.items():
            stages.append(build_stage(stage_name, stage_params or {}))

    return FilterPipeline(stages=stages) if stages else FilterPipeline()
