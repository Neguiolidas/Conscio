"""Workspace-aware, consent-gated structural ingestion (v1.7.2).

The distiller (v1.7.0) and its injection (v1.7.1) are powerful precisely because
they read a graph of real code — so *which* workspace's graph may be ingested is
an access-control decision, made per workspace and persisted. This module is that
policy layer, keyed off ``Workspace.id`` (v1.5).

Design constraints (non-negotiable):

- **Default OFF.** An unknown workspace consents to nothing. Ingestion is opt-in.
- **Switch-safe.** ``sync_structure`` unloads any loaded graph when the current
  workspace is not consented — one project's structure never leaks into another.
- **Parent only by explicit consent.** Reading the parent multi-project folder
  (``PARENT``) happens only when that scope was explicitly granted.

The graph is consumed as data, never code (R10) — see :mod:`conscio.structural`.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Where a workspace's graph lives, relative to the scoped root.
GRAPH_RELPATH = Path("graphify-out") / "graph.json"
# Where consent is persisted, relative to the engine storage dir.
CONSENT_FILENAME = "structural_consent.json"


class ConsentScope(Enum):
    OFF = "off"          # never ingest (default)
    PROJECT = "project"  # ingest this workspace's own graph
    PARENT = "parent"    # ingest the parent multi-project folder's graph


def consent_path(storage: str | Path) -> Path:
    """The consent store path for an engine storage dir (CLI + daemon agree)."""
    return Path(storage) / CONSENT_FILENAME


def _coerce(value: Any) -> ConsentScope:
    try:
        return ConsentScope(value)
    except ValueError:
        return ConsentScope.OFF


class StructuralConsent:
    """Per-``Workspace.id`` consent, persisted as a small JSON map.

    Tolerant of a missing or corrupt store (treated as "no consent for anyone").
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._map: dict[str, ConsentScope] = self._load()

    def _load(self) -> dict[str, ConsentScope]:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): _coerce(v) for k, v in raw.items()}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {k: s.value for k, s in self._map.items()}, indent=1))
        except OSError as exc:
            log.warning("structural consent save failed: %s", exc)

    def scope_for(self, workspace_id: str) -> ConsentScope:
        return self._map.get(workspace_id, ConsentScope.OFF)

    def grant(self, workspace_id: str, scope: ConsentScope) -> None:
        """Grant (or, for OFF, revoke) consent for a workspace; persists."""
        if scope is ConsentScope.OFF:
            self._map.pop(workspace_id, None)
        else:
            self._map[workspace_id] = scope
        self._save()

    def graph_path_for(
        self, workspace: Any, scope: Optional[ConsentScope] = None
    ) -> Optional[Path]:
        """Resolve the consented graph path for ``workspace``, or None.

        Returns a path only when the (explicit or stored) scope permits ingestion
        AND the graph file actually exists. OFF / missing file -> None.
        """
        scope = scope if scope is not None else self.scope_for(workspace.id)
        if scope is ConsentScope.PROJECT:
            root = Path(workspace.root)
        elif scope is ConsentScope.PARENT:
            root = Path(workspace.root).parent
        else:
            return None
        candidate = root / GRAPH_RELPATH
        return candidate if candidate.exists() else None


def sync_structure(engine: Any, workspace: Any, consent: StructuralConsent) -> str:
    """Bring the engine's loaded structure in line with consent for ``workspace``.

    Auto-loads the consented graph when present; otherwise ensures nothing is
    loaded (the switch-safety property). Never raises — a malformed graph unloads
    and reports ``load-error`` rather than killing the caller's loop.

    Returns a short status verb: ``loaded:<path>`` / ``unloaded`` / ``none`` /
    ``load-error``.
    """
    try:
        path = consent.graph_path_for(workspace)
    except Exception as exc:                       # defensive: bad workspace obj
        log.warning("consent path resolution failed: %s", exc)
        path = None

    if path is not None:
        try:
            engine.load_structure(path)
            return f"loaded:{path}"
        except Exception as exc:                   # malformed/oversized -> stay safe
            log.warning("structure load failed (%s); unloading", exc)
            engine.unload_structure()
            return "load-error"

    # no consent / no graph -> ensure nothing remains loaded
    if engine.structural_signal() is not None:
        engine.unload_structure()
        return "unloaded"
    return "none"
