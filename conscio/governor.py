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


BASELINE_NAME = "governor_baseline.json"

# A request that rebuilt a large prefix from nothing: the cache had expired or
# been invalidated. Measured at 12x the cost of a warm turn, so it is tracked
# separately rather than averaged away.
COLD_PREFIX_TOKENS = 50_000

# A window only 2x the prefix leaves too little working room: one large Read
# would trip compaction and the session would thrash.
MIN_HEADROOM_FACTOR = 2.0
# Margin over the observed post-compaction context. Measured 2026-08-01: with a
# 40,000 window the summary landed at 82,019, and with a 100,000 window at
# ~90,000 — the landing point is driven by content, not by the window. A ceiling
# at or under it makes compaction fire again immediately, forever.
FLOOR_MARGIN = 1.1
# What one compaction costs beyond the turn it replaces: the summary the model
# writes. The cache rebuild scales with the window and is modelled separately.
SUMMARY_OUT_TOKENS = 2_000
CANDIDATE_WINDOWS = (25_000, 40_000, 60_000, 80_000, 120_000, 160_000, 240_000)


def cost_units(row: dict) -> float:
    """Cost of one request in uncached-input-token equivalents."""
    return sum(row[k] * w for k, w in WEIGHTS.items())


def summarise(rows: list[dict]) -> dict:
    """Aggregate billed requests into the shape the report prints."""
    if not rows:
        return {"requests": 0, "avg_context": 0, "units": 0.0,
                "cr": 0, "cw": 0, "cold": 0, "cold_units": 0.0}
    cold = [r for r in rows if r["cr"] == 0 and r["cw"] > COLD_PREFIX_TOKENS]
    return {
        "requests": len(rows),
        "avg_context": sum(context_of(r) for r in rows) // len(rows),
        "units": sum(cost_units(r) for r in rows),
        "cr": sum(r["cr"] for r in rows),
        "cw": sum(r["cw"] for r in rows),
        "cold": len(cold),
        "cold_units": sum(cost_units(r) for r in cold),
    }


def growth_rate(rows: list[dict]) -> float:
    """Tokens of context added per request, from first to last."""
    if len(rows) < 2:
        return 0.0
    return max(0.0, (context_of(rows[-1]) - context_of(rows[0])) / (len(rows) - 1))


def compaction_floor(root: str | Path, sessions: int = 10) -> int:
    """Smallest context observed immediately after a compaction, or 0.

    This is the number a window must clear. It comes from the user's own
    transcripts because it depends on their prefix and their summariser output,
    not on anything we can assume.
    """
    floors: list[int] = []
    for path in _recent_transcripts(Path(root), sessions):
        rows: list[dict] = []
        marks: list[int] = []
        seen: set[str] = set()
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if event.get("isCompactSummary"):
                        marks.append(len(rows))
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
                        "out": int(usage.get("output_tokens") or 0)})
        except OSError:
            continue
        floors += [context_of(rows[b]) for b in marks if b < len(rows)]
    return min(floors) if floors else 0


def modelled_cost(window: int, *, prefix: int, requests: int,
                  growth: float, out_per_request: float) -> float:
    """Total modelled cost of a session at ``window``, in equivalent units.

    Two terms that pull against each other, which is why the answer is a curve
    and not "smaller is better":

      turns       — every request re-reads the context, so a smaller window makes
                    each turn cheaper;
      compaction  — a smaller window fills sooner, and each compaction pays to
                    rebuild the cache it invalidated plus the summary it writes.

    Measured on a real 881-request session, the unconstrained minimum sits near
    60,000 and the curve is flat from 40,000 to 80,000. Below 40,000 it climbs
    steeply. Once this host's landing floor is applied, 120,000 wins.
    """
    room = window - prefix
    if room <= 0:
        return float("inf")
    avg_context = (prefix + window) / 2
    turns = requests * (avg_context * WEIGHTS["cr"]
                        + out_per_request * WEIGHTS["out"])
    compactions = (growth * requests) / room
    each = (window * WEIGHTS["cw"] + avg_context * WEIGHTS["cr"]
            + SUMMARY_OUT_TOKENS * WEIGHTS["out"])
    return turns + compactions * each


def recommend_window(prefix: int, *, requests: int = 0, growth: float = 0.0,
                     out_per_request: float = 0.0, floor: int = 0) -> int:
    """The cost-optimal window for this profile, never below the hard floor.

    ``floor`` is the observed post-compaction context: a window at or under it
    cannot be satisfied, because the summariser lands above it and compaction
    fires again at once.
    """
    hard = max(int(prefix * MIN_HEADROOM_FACTOR), int(floor * FLOOR_MARGIN))
    usable = [w for w in CANDIDATE_WINDOWS if w >= hard]
    if not usable:
        return hard
    if requests <= 0 or growth <= 0:
        return usable[0]
    return min(usable, key=lambda w: modelled_cost(
        w, prefix=prefix, requests=requests, growth=growth,
        out_per_request=out_per_request))


def write_baseline(space_dir: str | Path, snapshot: dict) -> Path:
    """Freeze what the host looked like before the ceiling was applied.

    Kept beside obs.db rather than inside it: as an observation it would be
    subject to retention, and pruning away the reference the report compares
    against would silently turn every later report into a lie.
    """
    path = Path(space_dir) / BASELINE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def read_baseline(space_dir: str | Path) -> dict | None:
    """The frozen snapshot, or None when there is none to compare against."""
    try:
        return json.loads((Path(space_dir) / BASELINE_NAME).read_text("utf-8"))
    except Exception:
        return None


def _bar(fraction: float, width: int = 24) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "█" * filled + "░" * (width - filled)


def render_report(session: str, now: dict, baseline: dict | None,
                  window: int | None) -> str:
    """The savings report for one session.

    Only cache read and cache write appear: they are what a ceiling moves.
    Output is untouched by this version, and a zero row with a caveat beside it
    would be noise pretending to be information.
    """
    state = f"governor ON (window {window:,})" if window else "governor OFF"
    per = now["units"] / now["requests"] if now["requests"] else 0.0
    lines = [f"Session {session} · {now['requests']:,} turns · {state}", ""]
    lines.append(f"  {'Avg context/turn':<22}{now['avg_context']:>12,}")
    lines.append(f"  {'Cost (equiv. units)':<22}{now['units']:>12,.0f}")
    lines.append(f"  {'Per request':<22}{per:>12,.0f}")
    if now["cold"]:
        share = now["cold_units"] / now["units"] * 100 if now["units"] else 0.0
        lines.append(f"  {'Cold rebuilds':<22}{now['cold']:>12,}"
                     f"   ({share:.1f}% of cost)")

    if not baseline:
        lines += ["", "  No baseline recorded — run `conscio govern on` to freeze",
                  "  one. Absolute numbers only; nothing to compare against."]
        return "\n".join(lines)

    base_per = float(baseline.get("units_per_request") or 0.0)
    lines.insert(3, f"  {'Baseline context/turn':<22}"
                    f"{baseline.get('avg_context', 0):>12,}")
    if base_per > 0:
        saved = (base_per - per) / base_per
        lines += ["", f"  {'Saved':<22}{saved * 100:>11.1f}%",
                  f"  {_bar(max(0.0, saved))}"]
        if saved < 0:
            lines.append("  Context grew against the baseline — the ceiling is "
                         "not in effect, or the profile changed.")
    lines += ["", f"  {'Breakdown':<18}{'current':>12}",
              f"  {'cache read':<18}{now['cr']:>12,}",
              f"  {'cache write':<18}{now['cw']:>12,}"]
    return "\n".join(lines)


def report_for_session(path: str | Path, space_dir: str | Path,
                       window: int | None) -> str:
    """Render the report for one transcript file."""
    rows = read_usage(path)
    return render_report(Path(path).stem[:8], summarise(rows),
                         read_baseline(space_dir), window)
