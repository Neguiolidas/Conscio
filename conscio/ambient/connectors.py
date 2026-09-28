# conscio/ambient/connectors.py
"""Connectors wake an agent that is not live (§7.4-§7.6). A connector knows
its runtime's CLI and nothing about the board: the node claims the task,
builds the env and the prompt, and hands them over."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

SPAWN_REASONS = ("spawn_error", "timeout", "rate_limited")


@dataclass(frozen=True)
class Spawned:
    session_id: str
    pid: int | None


class SpawnFailed(Exception):
    """reason is one of SPAWN_REASONS: it becomes wake_failed (reason: <r>)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in SPAWN_REASONS:
            raise ValueError(f"unknown spawn failure {reason!r}")
        super().__init__(detail or reason)
        self.reason = reason


class Connector(Protocol):
    name: str

    def spawn(self, *, entry: dict, prompt: str, env: dict[str, str],
              cwd: str) -> Spawned: ...

    def is_active(self, session_id: str) -> bool | None: ...

    def stop(self, session_id: str) -> None: ...


_RATE_LIMITED = re.compile(r"\b429\b|rate[_ ]limit", re.IGNORECASE)
_BG_ID = re.compile(r"^backgrounded\s+\S+\s+([0-9a-f]{8})\s*$", re.MULTILINE)
_SCOPE = ["systemd-run", "--user", "--scope", "--collect", "--quiet"]
_STATE_ACTIVE = {"working": True, "done": False, "failed": False, "blocked": None}


def parse_bg_session_id(stdout: str) -> str | None:
    m = _BG_ID.search(stdout or "")
    return m.group(1) if m else None


def parse_agents(stdout: str) -> dict[str, bool | None] | None:
    """{short id: active}. None when the text is not the known JSON shape:
    liveness then says 'unknown', and unknown never spawns (I7)."""
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list) or not all(isinstance(i, dict) for i in data):
        return None
    return {str(i["id"]): _STATE_ACTIVE.get(str(i.get("state")))
            for i in data if i.get("id")}


class ClaudeBg:
    name = "claude-bg"

    def __init__(self, *, binary: str = "claude", run: Callable = subprocess.run,
                 timeout_s: float = 60.0) -> None:
        self.binary, self.run, self.timeout_s = binary, run, timeout_s

    def argv(self, *, entry: dict, prompt: str) -> list[str]:
        argv = [*_SCOPE, self.binary, "--bg"]      # A-1: own scope, survives the reactor
        if entry.get("model"):
            argv += ["--model", str(entry["model"])]
        argv.append(prompt)                        # I8: one element, the template bytes
        return argv

    def spawn(self, *, entry: dict, prompt: str, env: dict[str, str], cwd: str) -> Spawned:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                cp = self.run(self.argv(entry=entry, prompt=prompt), env=env, cwd=cwd,
                              stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                              timeout=self.timeout_s, check=False)       # A-4: files, not pipes
            except subprocess.TimeoutExpired as exc:
                raise SpawnFailed("timeout", str(exc)) from None
            except OSError as exc:
                raise SpawnFailed("spawn_error", str(exc)) from None
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode("utf-8", "replace")
            text = f"{stdout}\n{err.read().decode('utf-8', 'replace')}"
        if cp.returncode == 0:
            sid = parse_bg_session_id(stdout)
            if sid is not None:
                return Spawned(session_id=sid, pid=None)
        if _RATE_LIMITED.search(text):
            raise SpawnFailed("rate_limited", text[-200:])
        if cp.returncode != 0:
            raise SpawnFailed("spawn_error", f"exit {cp.returncode}: {text[-200:]}")
        raise SpawnFailed("spawn_error", "no session id in claude --bg output")

    def is_active(self, session_id: str) -> bool | None:
        try:
            cp = self.run([self.binary, "agents", "--json"], capture_output=True, text=True,
                          timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if cp.returncode != 0:
            return None
        table = parse_agents(cp.stdout or "")
        return None if table is None else table.get(session_id, False)

    def stop(self, session_id: str) -> None:
        cp = self.run([self.binary, "stop", session_id], capture_output=True, text=True,
                      timeout=15, check=False)
        if cp.returncode != 0:
            text = f"{cp.stdout or ''}\n{cp.stderr or ''}".strip()
            raise RuntimeError(f"exit {cp.returncode}: {text[-200:]}")


CONNECTORS: dict[str, Connector] = {"claude-bg": ClaudeBg()}
