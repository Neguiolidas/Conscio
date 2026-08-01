"""Measure and govern the host's context window.

Everything here reads Claude Code's own session transcripts
(``~/.claude/projects/<project>/<session>.jsonl``) and their ``message.usage``
blocks. That is the host's record of what was actually billed; counting tokens
ourselves would produce a number that is ours and not the invoice's.

Stdlib only. Pure functions over paths — nothing here holds state.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

TRANSCRIPT_GLOB = "*/*.jsonl"

# Relative price of each billed channel, in uncached-input-token equivalents.
WEIGHTS = {"in": 1.0, "cw": 1.25, "cr": 0.1, "out": 5.0}


def projects_dir() -> Path:
    """Where the host keeps session transcripts."""
    return Path(os.environ.get(
        "CLAUDE_DIR", str(Path.home() / ".claude"))) / "projects"


def read_usage(path: str | Path) -> list[dict]:
    """One row per billed request, in file order, de-duplicated by message id.

    A transcript may be appended to while we read it, so a trailing partial line
    is normal and is skipped rather than raised.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue                  # truncated tail, or not our shape
                message = event.get("message") or {}
                usage = message.get("usage") or {}
                if not usage:
                    continue
                mid = message.get("id")
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)
                rows.append({
                    "in": int(usage.get("input_tokens") or 0),
                    "cw": int(usage.get("cache_creation_input_tokens") or 0),
                    "cr": int(usage.get("cache_read_input_tokens") or 0),
                    "out": int(usage.get("output_tokens") or 0),
                    "ts": event.get("timestamp") or "",
                })
    except OSError:
        pass                                  # absent or unreadable: no rows
    return rows


def context_of(row: dict) -> int:
    """Tokens of context the model was sent, however they were billed."""
    return row["in"] + row["cw"] + row["cr"]


def _recent_transcripts(root: Path, limit: int) -> list[Path]:
    try:
        files = [p for p in Path(root).glob(TRANSCRIPT_GLOB) if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def measure_prefix(root: str | Path, sessions: int = 10) -> dict:
    """Median first-turn context across recent sessions.

    The first turn of a session carries the stable prefix — system prompt, tool
    schemas, CLAUDE.md, memory — plus the opening user message. It is the floor
    every later turn is built on, and therefore the floor any ceiling must clear.
    Measured at 45,101 and 50,270 in two fresh probe sessions, which is why a
    40,000 ceiling is not merely aggressive but impossible.

    The median, not the mean: one enormous session should not move the estimate.
    """
    firsts = []
    for path in _recent_transcripts(Path(root), sessions):
        rows = read_usage(path)
        if rows:
            firsts.append(context_of(rows[0]))
    if not firsts:
        return {"prefix": 0, "samples": 0, "sessions": 0}
    return {"prefix": int(statistics.median(firsts)),
            "samples": len(firsts), "sessions": len(firsts)}
