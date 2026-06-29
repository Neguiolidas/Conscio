"""Declarative extras registry. Today: Graphify only (Conscio already reads
graph.json; zero runtime dep). Obsidian/MemPalace are added here later as
their own opt-in plugin packages WITHOUT touching the wizard."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Extra:
    name: str
    summary: str
    optional_dep: "str | None"
    enable: Callable[[Path], list[str]]


def graphify_enable(space_dir: Path) -> list[str]:
    return ["graphify . --update",
            f"conscio consent --scope structure --storage {space_dir}"]


REGISTRY: dict[str, Extra] = {
    "graphify": Extra(
        name="graphify",
        summary="Structural cognition: build a code graph Conscio distills "
                "into ranked signal (reads graph.json; no runtime dependency).",
        optional_dep=None,
        enable=graphify_enable),
}
