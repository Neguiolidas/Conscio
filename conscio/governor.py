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


def _mtime(path: Path) -> float:
    """Modification time, or 0.0 if it cannot be read.

    The host is writing these files while we walk them, so one can be rotated
    away between the glob and the sort. An unhandled stat here would take down
    the whole report over a file we did not need; sorting it last is enough.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _recent_transcripts(root: Path, limit: int) -> list[Path]:
    try:
        files = [p for p in Path(root).glob(TRANSCRIPT_GLOB) if p.is_file()]
    except OSError:
        return []
    files.sort(key=_mtime, reverse=True)
    return files[:limit]


def measure_prefix(root: str | Path, sessions: int = 10) -> dict:
    """Median first-turn context across recent sessions.

    The first turn of a session carries the stable prefix — system prompt, tool
    schemas, CLAUDE.md, memory — plus the opening user message. It is the floor
    every later turn is built on, and therefore the floor any ceiling must clear.
    Measured at 45,101 and 50,270 in two fresh probe sessions, which is why a
    40,000 ceiling is not merely aggressive but impossible.

    The median, not the mean: one enormous session should not move the estimate.

    Known contaminant, measured 2026-08-01 and left in deliberately. A session
    resumed from an earlier one carries that history in its *first* billed turn,
    so this reads prefix-plus-history and calls it prefix: 112,167 for such a
    session against 38,490 for fresh ones in the same project. Every sample is
    weighted alike, so three-request throwaways vote with 5,796-request sessions.
    Both distortions are latent rather than wrong today -- the recommendation is
    driven by ``compaction_floor``, and the prefix only binds above roughly
    78,000, which no fresh session on this host reaches. Measured across four
    scopings (all projects, excluding probes, this project, this project's real
    sessions) the prefix moved 38,490-45,101 and the recommendation did not move
    at all. Worth fixing when a host appears whose prefix actually binds; not
    worth guessing at a resume-detection heuristic before then.
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


def growth_per_session(root: str | Path, sessions: int = 10) -> float:
    """Median tokens added per request, measured within each session.

    Never across a concatenation of sessions: the rows of two unrelated sessions
    laid end to end give a delta that means nothing and is frequently negative,
    which clamps to zero and silently removes the compaction term from the cost
    model.
    """
    rates = []
    for path in _recent_transcripts(Path(root), sessions):
        rate = growth_rate(read_usage(path))
        if rate > 0:
            rates.append(rate)
    return float(statistics.median(rates)) if rates else 0.0


def compaction_floor(root: str | Path, sessions: int = 10) -> int:
    """**Largest** context observed immediately after a compaction, or 0.

    This is the number a window must clear, and the worst case governs it: the
    question is "what ceiling does compaction always fit under", not "how low can
    it get". A minimum would permit a window just above the best landing, and any
    session that landed higher would compact, land above its own ceiling, and
    compact again. Measured landings on one host spanned 68,498 to 115,264, and a
    40,000 window — which a minimum would have allowed — was proven to loop.

    Derived from the user's own transcripts, because it depends on their prefix
    and their summariser output rather than on anything we can assume.
    """
    floors: list[tuple[str, int]] = []
    target = ""
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
                        "out": int(usage.get("output_tokens") or 0),
                        "model": str(message.get("model") or "")})
        except OSError:
            continue
        # Transcripts arrive newest-first, so the last billed row of the first
        # one that has any is the model in use right now -- and reading the last
        # row rather than the first means a mid-session model switch resolves to
        # what the session switched *to*.
        if not target and rows:
            target = rows[-1]["model"]
        # The row right after a compaction is often billed with no context at
        # all — 20 of 95 landings on this host. Taking it verbatim scores that
        # compaction as 0, and under max() a 0 does not lose harmlessly: it
        # drops the measurement, and a floor built from fewer landings than
        # actually occurred can only come out too low. Too low is the direction
        # that permits the loop this whole number exists to forbid. So scan on
        # to the first row that was really billed, stopping at the next
        # compaction — past it the context belongs to a different landing.
        stops = marks[1:] + [len(rows)]
        for start, stop in zip(marks, stops):
            hit = next((rows[i] for i in range(start, min(stop, len(rows)))
                        if context_of(rows[i]) > 0), None)
            if hit:
                floors.append((hit["model"], context_of(hit)))
    # A landing only describes the window it happened in. Pooling models put a
    # sonnet-5 landing of 142,208 under an opus-5 session whose own landings
    # topped out at 125,586, and since max() governs, the foreign number won:
    # hard_floor came out 156,428 instead of 138,144, which forbade the 140,000
    # candidate outright. On this host the recommendation was 160,000 either way
    # -- the cost model picks it on its own merits -- so the damage was not a
    # wrong number but an unfalsifiable one: 160,000 looked chosen while it was
    # being forced, and no measurement could tell the two apart. Same-model
    # landings only. Falling back to the pooled maximum when a model has no
    # landings of its own is deliberate: too high merely wastes headroom, while
    # returning 0 would drop the floor entirely and re-permit the compaction
    # loop that this whole number exists to forbid.
    same = [n for m, n in floors if m == target]
    return max(same or [n for _, n in floors] or [0])


def modelled_cost(window: int, *, prefix: int, requests: int,
                  growth: float, out_per_request: float) -> float:
    """Total modelled cost of a session at ``window``, in equivalent units.

    Two terms that pull against each other, which is why the answer is a curve
    and not "smaller is better":

      turns       — every request re-reads the context, so a smaller window makes
                    each turn cheaper;
      compaction  — a smaller window fills sooner, and each compaction pays to
                    rebuild the cache it invalidated plus the summary it writes.

    Valid only at or above ``hard_floor``. The compaction term assumes each
    compaction reclaims ``window - prefix``; under the landing floor it reclaims
    nothing and fires again, so the figure this returns there is not merely
    optimistic but wrong on its own premise. Callers must refuse those windows
    rather than print what this says about them.

    Measured on a real 881-request session, the unconstrained minimum sits near
    60,000 and the curve is flat from 40,000 to 80,000. Below 40,000 it climbs
    steeply. Once this host's landing floor is applied, 160,000 wins.
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


def hard_floor(prefix: int, landed: int) -> int:
    """The smallest window that can actually be satisfied.

    Two independent reasons a window is unusable, whichever binds harder:

      headroom — it must hold the stable prefix with room left to work in;
      landing  — it must sit above where the summariser actually lands, or the
                 compaction it triggers finishes *over* its own threshold and
                 fires again immediately. That loop is what this feature exists
                 to prevent, and it was reproduced live at window 40,000.

    One formula in one place: the recommendation and the curve that justifies it
    have to refuse the same windows, or the report argues with itself.
    """
    return max(int(prefix * MIN_HEADROOM_FACTOR), int(landed * FLOOR_MARGIN))


def recommend_window(prefix: int, *, requests: int = 0, growth: float = 0.0,
                     out_per_request: float = 0.0, floor: int = 0) -> int:
    """The cost-optimal window for this profile, never below the hard floor.

    ``floor`` is the observed post-compaction context: a window at or under it
    cannot be satisfied, because the summariser lands above it and compaction
    fires again at once.

    Returns **0** when there is no measured prefix. Without one the hard floor
    collapses to zero and the smallest candidate wins — 25,000, which was
    measured at 36.9% saving against 241 compactions. Guessing there is worse
    than declining to answer.
    """
    if prefix <= 0:
        return 0
    hard = hard_floor(prefix, floor)
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


def settings_path(scope: str = "local") -> Path:
    """Where to write the ceiling.

    Default is the project's ``.claude/settings.local.json``. Settings resolve
    local > project > user, so this wins reliably; it is gitignored, so it never
    reaches a teammate's checkout; and it scopes the ceiling to the project the
    operator turned it on in, rather than to every session on the machine.

    ``scope="global"`` targets ``~/.claude/settings.json`` for someone who really
    does want it everywhere.
    """
    if scope == "global":
        return Path(os.environ.get(
            "CLAUDE_DIR", str(Path.home() / ".claude"))) / "settings.json"
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())) \
        / ".claude" / "settings.local.json"


def current_window(scope: str = "local") -> int | None:
    """``autoCompactWindow`` as the host has it in ``scope``, or None.

    Verified 2026-08-01 that the host honours this key from settings.json even
    on builds whose /config screen exposes only an on/off toggle for
    auto-compaction: a probe with the key set compacted at the expected
    threshold with no environment variable in play.
    """
    try:
        value = json.loads(settings_path(scope).read_text("utf-8")).get(
            "autoCompactWindow")
    except Exception:
        return None
    return int(value) if isinstance(value, (int, float)) else None


def apply_window(window: int, *, ts: str, scope: str = "local") -> Path | None:
    """Write ``autoCompactWindow`` into the host settings, backing up first."""
    from .installer import hostcfg

    path = settings_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)

    def mutate(o: dict) -> None:
        o["autoCompactWindow"] = int(window)

    return hostcfg.backup_then_write_json(
        path, mutate=mutate,
        verify=lambda o: o.get("autoCompactWindow") == int(window), ts=ts)


def clear_window(*, ts: str, previous: int | None = None,
                 scope: str = "local") -> Path | None:
    """Undo the ceiling, restoring whatever the user had before.

    Deleting the key is only correct when there was no key. A user who had set
    their own ``autoCompactWindow`` before running ``govern on`` must get *their*
    value back, not the host default — otherwise `off` is not an undo, it is a
    second, silent change.
    """
    from .installer import hostcfg

    path = settings_path(scope)
    if not path.exists():
        return None

    def mutate(o: dict) -> None:
        if previous is None:
            o.pop("autoCompactWindow", None)
        else:
            o["autoCompactWindow"] = int(previous)

    return hostcfg.backup_then_write_json(
        path, mutate=mutate,
        verify=lambda o: o.get("autoCompactWindow") == previous, ts=ts)


def snapshot(space_dir: str | Path) -> dict:
    """Freeze what the host looks like right now, before a ceiling is applied."""
    from .timeutil import naive_utcnow

    paths = _recent_transcripts(projects_dir(), 10)
    rows: list[dict] = []
    for path in paths:
        rows.extend(read_usage(path))
    agg = summarise(rows)
    existing = read_baseline(space_dir)
    return {
        "prefix": measure_prefix(projects_dir())["prefix"],
        "avg_context": agg["avg_context"],
        "units_per_request": (agg["units"] / agg["requests"]
                              if agg["requests"] else 0.0),
        "requests": agg["requests"],
        # Kept so `govern off` can restore rather than delete, and so the report
        # can tell a genuine saving from the same work taking more turns. An
        # existing baseline wins: running `govern on` twice must not record the
        # governor's own window as if it were the user's.
        "prior_window": (existing.get("prior_window")
                         if existing is not None else current_window()),
        "turns_per_session": agg["requests"] / max(1, len(paths)),
        "taken_at": naive_utcnow().isoformat(timespec="seconds"),
    }


def report_all(space_dir: str | Path, window: int | None,
               sessions: int = 20) -> str:
    """One line per recent session, plus the aggregate."""
    paths = _recent_transcripts(projects_dir(), sessions)
    if not paths:
        return "No sessions found under " + str(projects_dir())
    baseline = read_baseline(space_dir)
    every: list[dict] = []
    lines = [(f"{'session':<12}{'project':<24}{'turns':>7}"
              f"{'avg ctx':>11}{'units':>14}")]
    for path in paths:
        rows = read_usage(path)
        if not rows:
            continue
        agg = summarise(rows)
        every.extend(rows)
        lines.append(f"{path.stem[:10]:<12}{path.parent.name[:22]:<24}"
                     f"{agg['requests']:>7,}{agg['avg_context']:>11,}"
                     f"{agg['units']:>14,.0f}")
    lines += ["", render_report(f"{len(paths)} sessions", summarise(every),
                                baseline, window)]
    return "\n".join(lines)
