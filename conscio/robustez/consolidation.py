"""The 9 processing rules + hard directives (v4.9 item 13; hindsight port, MIT).

_PROCESSING_RULES verbatim (prefer update over create, one observation
per distinct facet, no computation...); consolidation_prompt mounts the
prompt (mission first, then rules, then the decision guide, then the
output format). Directive is a stdlib dataclass with render() for prompt
injection; render_directives lists only active ones, highest priority
first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROCESSING_RULES = (
    "Prefer update over create: change an existing fact instead of adding a new one",
    "One observation per distinct facet: do not merge distinct facets into one fact",
    "No computation: never derive values that the source does not state",
    "Cite every fact: an uncited fact is ungrounded and cannot be trusted",
    "Preserve the original wording of quoted material",
    "Keep temporal qualifiers: 'since 2024' is not the same as 'in 2024'",
    "Prefer the source's own vocabulary over paraphrase",
    "A contradiction between sources is a finding, not something to resolve silently",
    "When in doubt, keep the old fact and record the new one as a candidate",
)


def consolidation_prompt(mission: str | None = None, *,
                         include_output_format: bool = True) -> str:
    """Mission (priority over the rules) + rules + decision guide + format."""
    parts: list[str] = []
    if mission:
        parts.append(f"Mission: {mission}")
    parts.append("Processing rules:")
    parts.extend(f"- {r}" for r in PROCESSING_RULES)
    if include_output_format:
        parts.append(
            "Output format: JSON object with keys 'facts' (list of"
            " {content, based_on}) and 'ops' (list of addressed operations).")
    return "\n".join(parts)


@dataclass
class Directive:
    name: str
    content: str
    priority: int = 0
    is_active: bool = True
    tags: list[str] = field(default_factory=list)
    id: str = ""
    created_ts: float = 0.0
    updated_ts: float = 0.0

    def render(self) -> str:
        tags = f" [{','.join(self.tags)}]" if self.tags else ""
        return f"{self.name}{tags}: {self.content}"


def render_directives(directives: list[Directive], tag: str | None = None) -> str:
    """Only active directives, highest priority first."""
    picked = [d for d in directives
              if d.is_active and (tag is None or tag in d.tags)]
    picked.sort(key=lambda d: -d.priority)
    return "\n".join(d.render() for d in picked)
