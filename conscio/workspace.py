"""WorkspaceContext — environment & workspace awareness (v1.5 "Live", component E).

Conscio runs in very different environments: IDEs (VS Code, Antigravity) and
CLIs (Claude Code, Codex) keep a **fixed** workspace for the session, while
agents (OpenClaw, Hermes) **switch** workspace per user request. The structural
layer (v1.6+) must scope its per-project index and consent to the *right*
workspace; the daemon and sensors must carry the *right* workspace identity.

This module is the dependency-free primitive that detects:
- the **workspace root** (explicit -> ``CONSCIO_WORKSPACE`` -> git-root walk -> cwd),
- a stable **id** (hash of the resolved root — the key v1.6 persists scope under),
- the **environment class** (``STABLE`` / ``SWITCHING`` / ``UNKNOWN``),

and emits a ``workspace:changed`` signal when the root id changes between polls.

v1.5 builds **only** detection + the change signal. Indexing, scope consent, and
persistence are v1.6 — which keys off ``Workspace.id`` and uses ``EnvClass`` to
decide how aggressively to re-check (STABLE: trust the root; SWITCHING/UNKNOWN:
re-confirm each cycle, the safer assumption).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Optional

# Environment hints (best-effort, never networked). Presence of any key marks the
# class; explicit CONSCIO_ENV_CLASS overrides all detection.
_STABLE_HINTS = (
    "TERM_PROGRAM",      # vscode / many IDE terminals
    "VSCODE_PID", "VSCODE_CWD", "VSCODE_IPC_HOOK_CLI",
    "CLAUDECODE", "CLAUDE_CODE",
    "CURSOR_TRACE_ID", "ANTIGRAVITY", "CODEX_SANDBOX",
)
_SWITCHING_HINTS = (
    "OPENCLAW", "OPENCLAW_SESSION", "OPENCLAW_WORKSPACE",
    "HERMES", "HERMES_SESSION", "HERMES_WORKSPACE",
)


class EnvClass(Enum):
    """How stable the workspace is for the running session."""
    STABLE = "stable"        # IDE/CLI: one workspace for the session
    SWITCHING = "switching"  # agent: workspace changes per task
    UNKNOWN = "unknown"      # treated as SWITCHING (the safer assumption)


@dataclass(frozen=True)
class Workspace:
    root: Path
    env: EnvClass
    id: str

    @property
    def recheck_each_cycle(self) -> bool:
        """Whether the daemon should re-resolve the workspace every cycle.

        Only a STABLE env is trusted to stay put; UNKNOWN is conservatively
        treated like SWITCHING and re-checked."""
        return self.env is not EnvClass.STABLE


class WorkspaceContext:
    """Detects the current workspace and signals changes.

    `emit` is an optional EventBus-style callable (``emit(type=, category=,
    data=)``); when supplied, `poll()` fires ``workspace:changed`` on a root
    change. `environ` is injectable for testing (defaults to ``os.environ``).
    """

    def __init__(self, *, explicit_root: Optional[str | Path] = None,
                 env: Optional[EnvClass] = None,
                 emit: Optional[Callable[..., object]] = None,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        self._environ: Mapping[str, str] = (
            environ if environ is not None else os.environ)
        self.explicit_root = Path(explicit_root) if explicit_root else None
        self._env_override = env
        self._emit = emit
        self._current = self.current()

    # ── detection ────────────────────────────────────────────────────────────
    def current(self) -> Workspace:
        root = self._resolve_root()
        env = self._env_override or self.classify_env(self._environ)
        return Workspace(root=root, env=env, id=self._workspace_id(root))

    def _resolve_root(self) -> Path:
        if self.explicit_root is not None:
            return self.explicit_root.resolve()
        env_root = self._environ.get("CONSCIO_WORKSPACE")
        if env_root:
            return Path(env_root).resolve()
        git_root = self._git_root(Path.cwd())
        if git_root is not None:
            return git_root
        return Path.cwd().resolve()

    @staticmethod
    def _git_root(start: Path) -> Optional[Path]:
        cur = start.resolve()
        for candidate in (cur, *cur.parents):
            if (candidate / ".git").exists():
                return candidate
        return None

    @staticmethod
    def _workspace_id(root: Path) -> str:
        return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def classify_env(environ: Mapping[str, str]) -> EnvClass:
        explicit = environ.get("CONSCIO_ENV_CLASS", "").strip().lower()
        if explicit in ("stable", "switching", "unknown"):
            return EnvClass(explicit)
        if any(k in environ for k in _SWITCHING_HINTS):
            return EnvClass.SWITCHING
        if any(k in environ for k in _STABLE_HINTS):
            return EnvClass.STABLE
        return EnvClass.UNKNOWN

    # ── change signal ─────────────────────────────────────────────────────────
    def poll(self) -> Workspace:
        """Re-resolve the workspace; emit workspace:changed if the id changed."""
        ws = self.current()
        if ws.id != self._current.id:
            previous = self._current
            self._current = ws
            if self._emit is not None:
                self._emit(type="workspace:changed", category="system",
                           data={"from": previous.id, "to": ws.id,
                                 "root": str(ws.root),
                                 "env": ws.env.value})
        return ws
