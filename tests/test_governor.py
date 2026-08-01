"""v3.9.2 Governor — measurement and reporting over host transcripts."""
import json
from pathlib import Path
from unittest import mock

from conscio import governor


def _write_session(projects_dir, project, name, turns):
    """Write a transcript in the host's shape: one JSON object per line.

    `turns` is a list of (input, cache_write, cache_read, output) tuples.
    """
    d = projects_dir / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i, (inp, cw, cr, out) in enumerate(turns):
            fh.write(json.dumps({
                "timestamp": f"2026-08-01T00:{i:02d}:00Z",
                "message": {"id": f"{name}-{i}", "role": "assistant", "usage": {
                    "input_tokens": inp,
                    "cache_creation_input_tokens": cw,
                    "cache_read_input_tokens": cr,
                    "output_tokens": out}}}) + "\n")
    return p


def test_read_usage_returns_one_row_per_billed_request(tmp_path):
    p = _write_session(tmp_path, "proj", "s1", [(2, 100, 0, 50), (2, 10, 100, 20)])
    rows = governor.read_usage(p)
    assert len(rows) == 2
    assert rows[0] == {"in": 2, "cw": 100, "cr": 0, "out": 50,
                       "ts": "2026-08-01T00:00:00Z"}


def test_read_usage_deduplicates_a_repeated_message_id(tmp_path):
    """Streaming can emit the same message id more than once; billing is once."""
    p = tmp_path / "dup.jsonl"
    line = json.dumps({"message": {"id": "same", "usage": {
        "input_tokens": 1, "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 0, "output_tokens": 5}}})
    p.write_text(line + "\n" + line + "\n", encoding="utf-8")
    assert len(governor.read_usage(p)) == 1


def test_read_usage_survives_a_truncated_line(tmp_path):
    """A transcript being written to can end mid-line — that is not an error."""
    p = _write_session(tmp_path, "proj", "s1", [(2, 100, 0, 50)])
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"message": {"id": "half", "usa')
    assert len(governor.read_usage(p)) == 1


def test_read_usage_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert governor.read_usage(tmp_path / "nope.jsonl") == []


def test_context_of_sums_every_billed_input_channel():
    assert governor.context_of({"in": 3, "cw": 100, "cr": 900, "out": 7}) == 1003


def test_measure_prefix_uses_the_median_first_turn(tmp_path):
    _write_session(tmp_path, "a", "s1", [(2, 20_000, 0, 5), (2, 100, 20_000, 5)])
    _write_session(tmp_path, "a", "s2", [(2, 30_000, 0, 5), (2, 100, 30_000, 5)])
    _write_session(tmp_path, "b", "s3", [(2, 40_000, 0, 5)])
    got = governor.measure_prefix(tmp_path)
    assert got["prefix"] == 30_002        # median of 20_002, 30_002, 40_002
    assert got["samples"] == 3
    assert got["sessions"] == 3


def test_measure_prefix_on_an_empty_dir_is_zero_not_a_crash(tmp_path):
    assert governor.measure_prefix(tmp_path) == {"prefix": 0, "samples": 0,
                                                 "sessions": 0}


# ── cost model + baseline (Task 2) ──────────────────────────────────────────

def test_cost_units_weights_each_channel_by_its_price():
    row = {"in": 100, "cw": 100, "cr": 100, "out": 100}
    assert governor.cost_units(row) == 100 + 125 + 10 + 500


def test_summarise_reports_averages_and_isolates_cold_requests():
    rows = [
        {"in": 2, "cw": 1_000, "cr": 100_000, "out": 500},
        {"in": 2, "cw": 1_000, "cr": 100_000, "out": 500},
        {"in": 2, "cw": 200_000, "cr": 0, "out": 500},       # cold
    ]
    s = governor.summarise(rows)
    assert s["requests"] == 3
    assert s["cold"] == 1
    assert s["avg_context"] == (101_002 + 101_002 + 200_002) // 3
    assert s["cw"] == 202_000 and s["cr"] == 200_000
    assert s["cold_units"] == governor.cost_units(rows[2])


def test_summarise_of_nothing_is_zeroed_not_a_division_by_zero():
    s = governor.summarise([])
    assert s["requests"] == 0 and s["avg_context"] == 0 and s["units"] == 0.0


def test_baseline_round_trips(tmp_path):
    snap = {"prefix": 45_101, "avg_context": 172_445, "units_per_request": 41_883.0,
            "requests": 800, "taken_at": "2026-08-01T00:00:00"}
    path = governor.write_baseline(tmp_path, snap)
    assert path.name == "governor_baseline.json"
    assert governor.read_baseline(tmp_path) == snap


def test_reading_a_missing_baseline_is_none_not_an_error(tmp_path):
    assert governor.read_baseline(tmp_path) is None


def test_a_corrupt_baseline_reads_as_none_rather_than_crashing(tmp_path):
    (tmp_path / "governor_baseline.json").write_text("{ truncated")
    assert governor.read_baseline(tmp_path) is None


def test_growth_rate_is_tokens_added_per_request():
    rows = [{"in": 0, "cw": 10_000, "cr": 0, "out": 0},
            {"in": 0, "cw": 0, "cr": 30_000, "out": 0}]
    assert governor.growth_rate(rows) == 20_000.0
    assert governor.growth_rate([]) == 0.0


def test_modelled_cost_is_u_shaped_not_monotonic():
    """Smaller is not always cheaper — that assumption cost this plan 5 points."""
    kw = {"prefix": 20_000, "requests": 881, "growth": 1_368.0,
          "out_per_request": 1_177.0}
    assert governor.modelled_cost(60_000, **kw) < governor.modelled_cost(25_000, **kw)
    assert governor.modelled_cost(60_000, **kw) < governor.modelled_cost(240_000, **kw)


def test_recommend_window_never_returns_one_below_the_compaction_floor():
    """A window under the observed landing floor re-compacts forever."""
    got = governor.recommend_window(45_101, requests=881, growth=1_368.0,
                                    out_per_request=1_177.0, floor=82_019)
    assert got >= int(82_019 * governor.FLOOR_MARGIN)
    assert got == 120_000, "the measured optimum above this host's floor"


def test_recommend_window_falls_back_when_there_is_no_usage_data():
    assert governor.recommend_window(20_000) >= 40_000


def test_compaction_floor_reads_post_compaction_contexts(tmp_path):
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    rows = [
        {"message": {"id": "a", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 300_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
        {"isCompactSummary": True},
        {"message": {"id": "b", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 70_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
    ]
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert governor.compaction_floor(tmp_path / "projects") == 70_002


def test_compaction_floor_is_zero_when_nothing_ever_compacted(tmp_path):
    _write_session(tmp_path / "projects", "p", "s1", [(2, 10_000, 0, 5)])
    assert governor.compaction_floor(tmp_path / "projects") == 0


# ── report (Task 3) ─────────────────────────────────────────────────────────

def test_report_shows_the_saving_against_the_baseline():
    now = governor.summarise([{"in": 2, "cw": 1_000, "cr": 40_000, "out": 500}] * 10)
    base = {"avg_context": 172_445, "units_per_request": 41_883.0,
            "prefix": 45_101, "requests": 800, "taken_at": "2026-08-01T00:00:00"}
    out = governor.render_report("abc123", now, base, 120_000)
    assert "abc123" in out
    assert "governor ON (window 120,000)" in out
    assert "Saved" in out and "%" in out
    assert "cache read" in out and "cache write" in out
    assert "output" not in out.lower(), "spec 6.1: no output row"


def test_report_without_a_baseline_refuses_to_invent_a_comparison():
    now = governor.summarise([{"in": 2, "cw": 1_000, "cr": 40_000, "out": 500}])
    out = governor.render_report("abc123", now, None, 120_000)
    assert "no baseline" in out.lower()
    assert "Saved" not in out


def test_report_says_governor_off_when_no_window_is_set():
    now = governor.summarise([{"in": 2, "cw": 1_000, "cr": 40_000, "out": 500}])
    assert "governor OFF" in governor.render_report("s", now, None, None)


def test_report_reports_a_regression_honestly():
    """If context grew instead of shrinking, the number must go negative."""
    now = governor.summarise([{"in": 2, "cw": 1_000, "cr": 300_000, "out": 500}] * 5)
    base = {"avg_context": 100_000, "units_per_request": 12_000.0,
            "prefix": 20_000, "requests": 100, "taken_at": "2026-08-01T00:00:00"}
    out = governor.render_report("s", now, base, 120_000)
    saved_line = next(ln for ln in out.splitlines() if "Saved" in ln)
    assert "-" in saved_line, "a regression must not read as a gain"


# ── CLI: prefix / status / on / off / report (Tasks 4-6) ────────────────────

def _cli():
    from conscio import cli
    return cli


def test_settings_path_defaults_to_the_project_local_file(tmp_path, monkeypatch):
    """Scoped to the project, gitignored, and highest precedence after managed."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert governor.settings_path().name == "settings.local.json"
    assert governor.settings_path().parent.name == ".claude"
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "home"))
    assert governor.settings_path("global").name == "settings.json"


def test_current_window_reads_the_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert governor.current_window() is None
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "settings.local.json").write_text(json.dumps({"autoCompactWindow": 120_000}))
    assert governor.current_window() == 120_000


def test_current_window_of_a_corrupt_settings_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "settings.local.json").write_text("{ not json")
    assert governor.current_window() is None


def _wire(tmp_path, monkeypatch, turns=None):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    _write_session(tmp_path / "projects", "p", "s1",
                   turns or [(2, 30_000, 0, 5), (2, 100, 60_000, 5)])
    return tmp_path / "space"


def test_govern_prefix_reports_measurement_recommendation_and_curve(
        tmp_path, monkeypatch, capsys):
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("prefix", None, str(space), False) == 0
    out = capsys.readouterr().out
    assert "30,002" in out                    # the measured prefix
    assert "Refused below" in out
    assert "Recommended" in out
    assert "modelled cost" in out             # the curve, not a bare number
    line = next(ln for ln in out.splitlines() if ln.startswith("Recommended"))
    assert int(line.split()[1].replace(",", "")) >= 60_004   # clears prefix x2


def test_the_curve_never_prints_a_bare_cost_for_a_window_it_would_refuse(
        tmp_path, monkeypatch, capsys):
    """A refused row showing an attractive number is an invitation to the bug.

    modelled_cost assumes a compaction reclaims `window - prefix`. Below the
    landing floor it reclaims nothing and fires again, so the figure is wrong on
    the model's own terms — and it is precisely the cheap-looking figure a user
    would override the recommendation to chase.
    """
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("prefix", None, str(space), False) == 0
    out = capsys.readouterr().out
    floor = int(next(ln for ln in out.splitlines()
                     if ln.startswith("Refused below")).split()[2].replace(",", ""))
    assert floor > governor.CANDIDATE_WINDOWS[0], "test needs a binding floor"
    for ln in out.splitlines():
        head = ln.strip().split()
        if not head or not head[0].replace(",", "").isdigit():
            continue
        w = int(head[0].replace(",", ""))
        if w < floor:
            assert "refused" in ln, f"window {w:,} is below the floor but reads as usable"


def test_the_floor_the_curve_draws_is_the_floor_the_recommendation_uses(
        tmp_path, monkeypatch, capsys):
    """One formula, one place. Two copies drift and the table stops agreeing
    with the line above it."""
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("prefix", None, str(space), False) == 0
    out = capsys.readouterr().out
    floor = int(next(ln for ln in out.splitlines()
                     if ln.startswith("Refused below")).split()[2].replace(",", ""))
    best = int(next(ln for ln in out.splitlines()
                    if ln.startswith("Recommended")).split()[1].replace(",", ""))
    assert best >= floor
    assert governor.hard_floor(
        governor.measure_prefix(governor.projects_dir())["prefix"],
        governor.compaction_floor(governor.projects_dir())) == floor


def test_hard_floor_reports_whichever_constraint_binds():
    # landing floor binds: 142,208 x 1.1 beats 45,101 x 2
    assert governor.hard_floor(45_101, 142_208) == 156_428
    # headroom binds: nothing ever compacted, so only the prefix constrains
    assert governor.hard_floor(45_101, 0) == 90_202


def test_govern_status_says_off_when_nothing_is_set(tmp_path, monkeypatch, capsys):
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("status", None, str(space), False) == 0
    assert "OFF" in capsys.readouterr().out


def test_govern_on_refuses_a_window_with_no_working_room(
        tmp_path, monkeypatch, capsys):
    space = _wire(tmp_path, monkeypatch)
    rc = _cli()._cmd_govern("on", 40_000, str(space), False)
    out = capsys.readouterr().out
    assert rc == 1, "40,000 is below 30,002 x 2 — must refuse"
    assert "30,002" in out and "60,004" in out
    assert governor.current_window() is None, "a refusal must not write anything"


def test_govern_on_applies_the_window_and_freezes_a_baseline(
        tmp_path, monkeypatch, capsys):
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("on", 120_000, str(space), False) == 0
    assert governor.current_window() == 120_000
    base = governor.read_baseline(space)
    assert base and base["avg_context"] > 0 and base["units_per_request"] > 0
    assert "govern off" in capsys.readouterr().out, "must say how to revert"


def test_govern_on_preserves_other_settings(tmp_path, monkeypatch):
    space = _wire(tmp_path, monkeypatch)
    d = tmp_path / "proj" / ".claude"
    d.mkdir(parents=True)
    (d / "settings.local.json").write_text(json.dumps({"effortLevel": "high"}))
    _cli()._cmd_govern("on", 120_000, str(space), False)
    data = json.loads((d / "settings.local.json").read_text())
    assert data["effortLevel"] == "high"
    assert data["autoCompactWindow"] == 120_000


def test_govern_off_restores_a_window_the_user_had_set_themselves(
        tmp_path, monkeypatch):
    """`off` must be an undo. Deleting a key the user owned is a second change."""
    space = _wire(tmp_path, monkeypatch)
    d = tmp_path / "proj" / ".claude"
    d.mkdir(parents=True)
    (d / "settings.local.json").write_text(json.dumps({"autoCompactWindow": 150_000}))
    _cli()._cmd_govern("on", 120_000, str(space), False)
    assert governor.current_window() == 120_000
    _cli()._cmd_govern("off", None, str(space), False)
    assert governor.current_window() == 150_000, "must restore, not delete"


def test_govern_off_removes_the_window_when_there_was_none_before(
        tmp_path, monkeypatch):
    space = _wire(tmp_path, monkeypatch)
    _cli()._cmd_govern("on", 120_000, str(space), False)
    assert _cli()._cmd_govern("off", None, str(space), False) == 0
    assert governor.current_window() is None


def test_govern_off_when_never_on_is_not_an_error(tmp_path, monkeypatch):
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("off", None, str(space), False) == 0


def test_govern_on_without_a_window_uses_the_recommendation(tmp_path, monkeypatch):
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("on", None, str(space), False) == 0
    assert governor.current_window() >= 60_004


def test_govern_report_prints_the_current_session(tmp_path, monkeypatch, capsys):
    space = _wire(tmp_path, monkeypatch)
    assert _cli()._cmd_govern("report", None, str(space), False) == 0
    out = capsys.readouterr().out
    assert "turns" in out and "Avg context/turn" in out
    assert "no baseline" in out.lower()


def test_govern_report_all_covers_every_session(tmp_path, monkeypatch, capsys):
    space = _wire(tmp_path, monkeypatch)
    _write_session(tmp_path / "projects", "q", "s2", [(2, 20_000, 0, 500)])
    assert _cli()._cmd_govern("report", None, str(space), True) == 0
    out = capsys.readouterr().out
    assert "s1" in out and "s2" in out and "sessions" in out


def test_report_all_with_no_transcripts_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    assert _cli()._cmd_govern("report", None, str(tmp_path / "space"), True) == 0
    assert "no sessions" in capsys.readouterr().out.lower()


def test_recommend_window_refuses_to_guess_without_a_measured_prefix():
    """With no transcripts the smallest candidate is 25,000 — measured
    catastrophic (36.9%, 241 compactions). Returning it would be the worst
    possible default, so an unmeasurable profile yields no recommendation."""
    assert governor.recommend_window(0) == 0
    assert governor.recommend_window(0, requests=100, growth=1000.0) == 0


def test_govern_on_refuses_when_there_is_nothing_to_measure(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    (tmp_path / "projects").mkdir()
    rc = _cli()._cmd_govern("on", None, str(tmp_path / "space"), False)
    assert rc == 1
    assert "no transcripts" in capsys.readouterr().out.lower()
    assert governor.current_window() is None


def test_govern_on_twice_still_restores_the_users_original_window(
        tmp_path, monkeypatch):
    """`on` twice must not record the governor's own window as the user's."""
    space = _wire(tmp_path, monkeypatch)
    d = tmp_path / "proj" / ".claude"
    d.mkdir(parents=True)
    (d / "settings.local.json").write_text(json.dumps({"autoCompactWindow": 150_000}))
    _cli()._cmd_govern("on", 120_000, str(space), False)
    _cli()._cmd_govern("on", 160_000, str(space), False)
    assert governor.current_window() == 160_000
    _cli()._cmd_govern("off", None, str(space), False)
    assert governor.current_window() == 150_000, "the user's own value, not 120,000"


def test_compaction_floor_ignores_zero_context_rows(tmp_path):
    """A zero-usage row must never be reported as where compaction landed.

    Real transcripts are full of them — 20 of 95 observed landings on the
    development host. This pins the output contract, not a guard: since the
    floor takes the worst landing, a zero simply loses, and the explicit filter
    that once served the earlier min() has been removed as dead code.
    """
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    rows = [
        {"message": {"id": "a", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 300_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
        {"isCompactSummary": True},
        {"message": {"id": "b", "usage": {"input_tokens": 0,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "output_tokens": 0}}},                       # the poison row
    ]
    (d / "zero.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    good = [
        {"message": {"id": "c", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 300_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
        {"isCompactSummary": True},
        {"message": {"id": "d", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 70_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
    ]
    (d / "good.jsonl").write_text("\n".join(json.dumps(r) for r in good) + "\n")
    assert governor.compaction_floor(tmp_path / "projects") == 70_002


def test_a_landing_behind_an_unbilled_row_is_still_measured(tmp_path):
    """Scoring that compaction 0 would drop it, and a dropped landing lowers
    the floor. 19 of 95 landings on the development host sat behind such a row.
    """
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    rows = [
        {"message": {"id": "a", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 300_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
        {"isCompactSummary": True},
        {"message": {"id": "b", "usage": {"input_tokens": 0,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "output_tokens": 0}}},                       # billed nothing
        {"message": {"id": "c", "usage": {"input_tokens": 8,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 96_000,
         "output_tokens": 5}}},                       # where it really landed
    ]
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert governor.compaction_floor(tmp_path / "projects") == 96_008


def test_a_landing_is_never_borrowed_from_the_next_compaction(tmp_path):
    """Scanning forward must stop at the next summary.

    Otherwise a compaction that produced only unbilled rows adopts the landing
    of the one after it, inventing a floor no compaction actually reached.
    """
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    rows = [
        {"message": {"id": "a", "usage": {"input_tokens": 2,
         "cache_creation_input_tokens": 300_000, "cache_read_input_tokens": 0,
         "output_tokens": 5}}},
        {"isCompactSummary": True},
        {"message": {"id": "b", "usage": {"input_tokens": 0,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "output_tokens": 0}}},
        {"isCompactSummary": True},
        {"message": {"id": "c", "usage": {"input_tokens": 1,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 130_000,
         "output_tokens": 5}}},
    ]
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert governor.compaction_floor(tmp_path / "projects") == 130_001


def test_a_transcript_rotated_away_mid_walk_does_not_sink_the_report(tmp_path):
    """The host writes these files while we read them."""
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text("{}\n")
    (d / "b.jsonl").write_text("{}\n")
    real = Path.stat

    def vanishing(self, *a, **kw):
        if self.name == "a.jsonl":
            raise FileNotFoundError(self)
        return real(self, *a, **kw)

    with mock.patch.object(Path, "stat", vanishing):
        got = governor._recent_transcripts(tmp_path / "projects", 10)
    assert [p.name for p in got] == ["b.jsonl", "a.jsonl"], \
        "the readable transcript must survive, the vanished one sorts last"


def test_growth_per_session_does_not_span_unrelated_sessions(tmp_path):
    """Concatenating sessions makes the delta meaningless — often negative."""
    _write_session(tmp_path / "projects", "p", "big",
                   [(2, 100_000, 0, 5), (2, 100, 140_000, 5)])
    _write_session(tmp_path / "projects", "p", "small",
                   [(2, 10_000, 0, 5), (2, 100, 20_000, 5)])
    got = governor.growth_per_session(tmp_path / "projects")
    assert got > 0, "a concatenation would give 0 here"
    assert got == 25_100.0        # median of 40,100 and 10,100


def test_growth_per_session_is_zero_when_there_is_nothing_to_measure(tmp_path):
    (tmp_path / "projects").mkdir()
    assert governor.growth_per_session(tmp_path / "projects") == 0.0


def test_compaction_floor_takes_the_worst_landing_not_the_best(tmp_path):
    """The floor answers "what window always holds", so the worst case governs.

    Taking min() says compaction *can* land low and permits a window just above
    that — but a session that lands high would then compact, land above its own
    ceiling, and compact again. Measured landings on one host ranged 68,498 to
    115,264; a min-based floor would have allowed 40,000, which was proven to loop.
    """
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)

    def _sess(name, landing):
        rows = [
            {"message": {"id": f"{name}a", "usage": {"input_tokens": 2,
             "cache_creation_input_tokens": 300_000,
             "cache_read_input_tokens": 0, "output_tokens": 5}}},
            {"isCompactSummary": True},
            {"message": {"id": f"{name}b", "usage": {"input_tokens": 0,
             "cache_creation_input_tokens": landing,
             "cache_read_input_tokens": 0, "output_tokens": 5}}},
        ]
        (d / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")

    _sess("low", 30_000)
    _sess("high", 115_000)
    assert governor.compaction_floor(tmp_path / "projects") == 115_000
