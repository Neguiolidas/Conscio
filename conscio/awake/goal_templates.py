"""GoalTemplates — deterministic signal→goal mapping for Awake Mode.

Zero LLM, zero random. Parses the world_state string produced by
Daemon.assemble() and returns a list of concrete goal descriptions
suitable for injection into the cognitive cycle.
"""
from __future__ import annotations

import re

_GROUP_THRESHOLD = 4


def goals_from_world_state(world_state: str) -> list[str]:
    """Derive contextual goals from the assembled world_state string."""
    if not world_state or not world_state.strip():
        return []
    goals: list[str] = []
    goals.extend(_filesystem_goals(world_state))
    goals.extend(_git_goals(world_state))
    return goals


def _filesystem_goals(ws: str) -> list[str]:
    modified = re.findall(r"modified:\s*(.+)", ws)
    created = re.findall(r"created:\s*(.+)", ws)
    deleted = re.findall(r"deleted:\s*(.+)", ws)

    # .py files that are NOT test files
    py_files = [
        f for f in modified + created
        if f.endswith(".py") and not _is_test_file(f)
    ]

    goals: list[str] = []

    if len(py_files) > _GROUP_THRESHOLD:
        goals.append(f"revisar {len(py_files)} arquivos .py modificados")
    elif py_files:
        for f in py_files:
            goals.append(f"verificar se testes cobrem {f}")

    if deleted:
        if len(deleted) > _GROUP_THRESHOLD:
            goals.append(f"investigar {len(deleted)} arquivos deletados")
        else:
            for f in deleted:
                goals.append(f"verificar impacto de {f} deletado")

    return goals


def _git_goals(ws: str) -> list[str]:
    commits = re.findall(r"commit\s+([0-9a-f]+)\s+by\s+\S+:\s*(.+)", ws)
    if not commits:
        return []
    if len(commits) > _GROUP_THRESHOLD:
        return [f"revisar {len(commits)} commits novos"]
    goals: list[str] = []
    for h, _subject in commits:
        goals.append(f"revisar diff {h}")
    return goals


def _is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")
