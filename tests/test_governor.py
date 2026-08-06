"""v3.9.2 Governor — measurement and reporting over host transcripts."""
import errno
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


def _freeze(space_dir, snapshot):
    """Record a baseline, as `govern on` would."""
    return governor.write_baseline(space_dir, snapshot)


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
    assert governor.cost_units(row) == 100 + 200 + 10 + 500


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
    # Was 120,000 while the cost model priced room as `window - prefix`. With the
    # landing point as the post-compaction floor, 120k leaves ~38k of room and
    # re-compacts often enough to lose to 160k (21.1M vs 22.3M units); 240k costs
    # more again (23.2M), so the curve is still U-shaped, just centred higher.
    assert got == 160_000, "the measured optimum above this host's floor"


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


def _compacted(path, model, landing, *, mtime):
    """One session that compacted once and landed at ``landing`` on ``model``."""
    import os
    rows = [
        {"message": {"id": f"{model}-a", "model": model, "usage": {
            "input_tokens": 0, "cache_creation_input_tokens": 300_000,
            "cache_read_input_tokens": 0, "output_tokens": 5}}},
        {"isCompactSummary": True},
        {"message": {"id": f"{model}-b", "model": model, "usage": {
            "input_tokens": 0, "cache_creation_input_tokens": landing,
            "cache_read_input_tokens": 0, "output_tokens": 5}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    os.utime(path, (mtime, mtime))


def test_a_landing_from_another_model_cannot_set_this_model_s_floor(tmp_path):
    """Measured on this host: a sonnet-5 landing of 142,208 was governing an
    opus-5 session whose own landings never passed 125,586. max() let the
    foreign number win, inflating the recommendation by ~20,000 and compacting
    less often than the evidence supported."""
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    _compacted(d / "old.jsonl", "claude-sonnet-5", 142_208, mtime=1000)
    _compacted(d / "new.jsonl", "claude-opus-5", 125_586, mtime=2000)
    assert governor.compaction_floor(tmp_path / "projects") == 125_586


def test_a_model_with_no_landings_of_its_own_keeps_a_floor(tmp_path):
    """Never 0 — that would drop the guard and re-permit the compaction loop."""
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    _compacted(d / "old.jsonl", "claude-sonnet-5", 142_208, mtime=1000)
    _write_session(tmp_path / "projects", "p", "fresh", [(2, 10_000, 0, 5)])
    import os
    os.utime(d / "fresh.jsonl", (3000, 3000))
    assert governor.compaction_floor(tmp_path / "projects") == 142_208


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


def test_report_flags_a_ceiling_that_is_not_being_enforced():
    """An average above the ceiling is proof the ceiling is not applying.

    Under enforcement context oscillates between the post-compaction floor and
    the ceiling, so the mean necessarily sits below the ceiling. A mean above
    it cannot happen while the host is compacting, and the report has both
    numbers already — it printed "governor ON (window 160,000)" directly above
    "Avg context/turn 188,606" without noticing the contradiction.

    The case is easy to miss precisely because it flatters the breakdown: a
    host that never compacts writes no cache, so cache write/turn collapses and
    reads as a large saving. Growth and thrift look alike in that row; only the
    window tells them apart.
    """
    now = governor.summarise([{"in": 2, "cw": 100, "cr": 188_000, "out": 500}] * 10)
    base = {"avg_context": 168_835, "units_per_request": 30_217.0,
            "cr_per_request": 164_163.0, "cw_per_request": 4_446.0,
            "prefix": 63_532, "requests": 14_162, "taken_at": "2026-08-01T00:00:00"}
    out = governor.render_report("abc123", now, base, 160_000)
    assert "not in effect" in out.lower()
    assert "160,000" in out


def test_report_stays_quiet_when_the_ceiling_is_holding():
    """The warning must not fire on the ordinary case it is meant to contrast.

    Same shape as above but with the average below the ceiling, which is what
    an enforced ceiling produces.
    """
    now = governor.summarise([{"in": 2, "cw": 4_000, "cr": 120_000, "out": 500}] * 10)
    base = {"avg_context": 168_835, "units_per_request": 30_217.0,
            "cr_per_request": 164_163.0, "cw_per_request": 4_446.0,
            "prefix": 63_532, "requests": 14_162, "taken_at": "2026-08-01T00:00:00"}
    out = governor.render_report("abc123", now, base, 160_000)
    assert "not in effect" not in out.lower()


def test_report_does_not_flag_enforcement_when_no_ceiling_is_set():
    """With the governor off there is no ceiling to be unenforced."""
    now = governor.summarise([{"in": 2, "cw": 100, "cr": 188_000, "out": 500}] * 10)
    base = {"avg_context": 168_835, "units_per_request": 30_217.0,
            "cr_per_request": 164_163.0, "cw_per_request": 4_446.0,
            "prefix": 63_532, "requests": 14_162, "taken_at": "2026-08-01T00:00:00"}
    out = governor.render_report("abc123", now, base, None)
    assert "not in effect" not in out.lower()


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


class TestTheSavedLine:
    """spec §6.1 draws `Saved` with two figures: what the ceiling took off the
    bill, and what share of it that was. A percentage alone cannot be sized —
    40% of a rounding error and 40% of a month's spend read identically."""

    def _line(self, base_per=41_883.0, turns=10):
        now = governor.summarise(
            [{"in": 2, "cw": 1_000, "cr": 40_000, "out": 500}] * turns)
        out = governor.render_report("s", now, {
            "avg_context": 172_445, "units_per_request": base_per,
            "prefix": 45_101, "requests": 800,
            "taken_at": "2026-08-01T00:00:00"}, 120_000)
        return next(ln for ln in out.splitlines() if "Saved" in ln)

    def test_the_line_carries_the_absolute_and_the_share(self):
        # 10 turns at 8,502 units each, against a baseline rate of 41,883.
        assert self._line().split() == ["Saved", "333,810", "79.7%"]

    def test_the_absolute_is_priced_over_the_turns_actually_taken(self):
        """Not over the baseline's own 800 turns — those are not turns this
        ceiling could have changed."""
        assert self._line(turns=5).split()[1] == "166,905"

    def test_the_two_figures_cannot_disagree(self):
        """One statement twice. If they are computed apart they drift apart."""
        _, absolute, share = self._line().split()
        units = float(absolute.replace(",", ""))
        would_have = 41_883.0 * 10
        assert abs(units / would_have * 100 - float(share.rstrip("%"))) < 0.05

    def test_a_regression_shows_a_negative_on_both(self):
        _, absolute, share = self._line(base_per=1_000.0).split()
        assert absolute.startswith("-") and share.startswith("-")


class TestBreakdownColumns:
    """spec §6.1 draws the breakdown as `current | baseline | saved`. It
    shipped with only the first column, which reads as a table that lost its
    right-hand side — and the baseline it would have compared against was
    never frozen.
    """

    def _now(self, turns=10):
        return governor.summarise(
            [{"in": 2, "cw": 1_000, "cr": 40_000, "out": 500}] * turns)

    def _base(self, **extra):
        base = {"avg_context": 172_445, "units_per_request": 41_883.0,
                "prefix": 45_101, "requests": 800,
                "taken_at": "2026-08-01T00:00:00"}
        base.update(extra)
        return base

    def _row(self, out, label):
        return next(ln for ln in out.splitlines() if ln.strip().startswith(label))

    def test_the_breakdown_carries_all_three_columns(self):
        out = governor.render_report("s", self._now(), self._base(
            cr_per_request=80_000.0, cw_per_request=2_000.0), 120_000)
        header = self._row(out, "Breakdown")
        assert header.split() == ["Breakdown", "current", "baseline", "saved"]
        read = self._row(out, "cache read/turn").split()
        assert read[-3:] == ["40,000", "80,000", "50.0%"]

    def test_the_figures_are_per_turn_not_totals(self):
        """Ten turns of 40,000 is 400,000 read — against a baseline of 800
        turns, printing totals would compare turn counts, not efficiency."""
        out = governor.render_report("s", self._now(turns=10), self._base(
            cr_per_request=40_000.0, cw_per_request=1_000.0), 120_000)
        assert "400,000" not in out
        assert self._row(out, "cache read/turn").split()[-1] == "0.0%"

    def test_a_baseline_frozen_before_the_fields_existed_shows_no_number(self):
        """The old snapshot has no cr/cw. Zero would render as a 100% loss on
        one side or a 100% gain on the other; both are inventions."""
        out = governor.render_report("s", self._now(), self._base(), 120_000)
        assert self._row(out, "cache read/turn").split()[-2:] == ["—", "—"]
        assert "govern on" in out, "must say how to get the comparison"
        assert "100.0%" not in out and "-100.0%" not in out

    def test_a_zero_baseline_row_is_not_a_hundred_percent_saving(self):
        out = governor.render_report("s", self._now(), self._base(
            cr_per_request=0.0, cw_per_request=1_000.0), 120_000)
        read = self._row(out, "cache read/turn").split()
        assert read[-2:] == ["0", "—"]
        assert "before v3.9.4" not in out, "the fields are present, just zero"

    def test_a_regression_in_a_row_reads_negative(self):
        out = governor.render_report("s", self._now(), self._base(
            cr_per_request=20_000.0, cw_per_request=1_000.0), 120_000)
        assert self._row(out, "cache read/turn").split()[-1] == "-100.0%"

    def test_govern_on_freezes_the_cache_breakdown(self, tmp_path, monkeypatch):
        """The report can only compare what `on` bothered to record."""
        space = _wire(tmp_path, monkeypatch)
        assert _cli()._cmd_govern("on", 120_000, str(space), False) == 0
        base = governor.read_baseline(space)
        assert base is not None
        # _wire writes two turns: 60,000 read and 30,100 written in total.
        assert base["cr_per_request"] == 30_000.0
        assert base["cw_per_request"] == 15_050.0


# ── the baseline cut: only turns the ceiling could have changed ─────────────

class TestSince:
    """A ceiling applies from the moment it is set. Turns older than that are
    the ones the baseline itself measured, so counting them as "current"
    compares the baseline against itself."""

    def _rows(self):
        return [{"in": 1, "cw": 0, "cr": 0, "out": 1,
                 "ts": f"2026-08-01T00:{m:02d}:00Z"} for m in (0, 4, 6, 9)]

    def test_only_turns_from_the_freeze_onward_are_current(self):
        kept = governor.since(self._rows(), "2026-08-01T00:05:00")
        assert [r["ts"] for r in kept] == ["2026-08-01T00:06:00Z",
                                           "2026-08-01T00:09:00Z"]

    def test_the_turn_at_the_freeze_instant_counts(self):
        """The boundary is inclusive: the freeze and the ceiling are one action,
        so the turn stamped at that second was taken under it."""
        kept = governor.since(self._rows(), "2026-08-01T00:04:00")
        assert len(kept) == 3

    def test_no_baseline_time_keeps_every_turn(self):
        rows = self._rows()
        assert governor.since(rows, None) == rows
        assert governor.since(rows, "") == rows

    def test_an_unparseable_baseline_time_keeps_every_turn(self):
        """Better to over-report turns than to silently report none."""
        rows = self._rows()
        assert governor.since(rows, "whenever") == rows

    def test_an_offset_is_converted_not_compared_as_text(self):
        """`02:30+03:00` is 23:30 the previous day — before a 00:05 cut. Compared
        as strings it sorts after it, and the turn would be counted as governed."""
        row = {"in": 1, "cw": 0, "cr": 0, "out": 1,
               "ts": "2026-08-01T02:30:00+03:00"}
        assert governor.since([row], "2026-08-01T00:05:00") == []

    def test_a_turn_with_no_timestamp_is_left_out(self):
        """It cannot be placed, and counting it as governed inflates a saving."""
        row = {"in": 1, "cw": 0, "cr": 0, "out": 1, "ts": ""}
        assert governor.since([row], "2026-08-01T00:05:00") == []


class TestReportCutsAtTheBaseline:
    """The defect this guards: a long session that straddles `govern on` mixed
    its ungoverned turns into the current figure, so a working ceiling reported
    a small loss."""

    # Costs 33,752 units — the ungoverned profile, above the baseline.
    BEFORE = (2, 1_000, 300_000, 500)
    # Costs 7,752 units — what the ceiling brought it down to.
    AFTER = (2, 1_000, 40_000, 500)
    BASE = {"avg_context": 150_000, "units_per_request": 20_000.0,
            "prefix": 40_000, "requests": 900,
            "taken_at": "2026-08-01T00:05:00"}

    def _session(self, tmp_path):
        return _write_session(tmp_path / "projects", "p", "straddle",
                              [self.BEFORE] * 5 + [self.AFTER] * 5)

    def test_a_working_ceiling_does_not_report_a_loss(self, tmp_path):
        path = self._session(tmp_path)
        _freeze(tmp_path / "space", self.BASE)
        out = governor.report_for_session(path, tmp_path / "space", 120_000)
        saved = next(ln for ln in out.splitlines() if "Saved" in ln)
        assert "-" not in saved, f"governed turns cost less than the baseline: {saved}"

    def test_the_figures_describe_the_governed_turns_only(self, tmp_path):
        path = self._session(tmp_path)
        _freeze(tmp_path / "space", self.BASE)
        out = governor.report_for_session(path, tmp_path / "space", 120_000)
        assert "41,002" in out, "avg context of the AFTER turns"
        assert "301,002" not in out

    def test_the_header_says_how_many_turns_were_left_out(self, tmp_path):
        path = self._session(tmp_path)
        _freeze(tmp_path / "space", self.BASE)
        out = governor.report_for_session(path, tmp_path / "space", 120_000)
        assert "5 turns since baseline (5 earlier)" in out

    def test_with_no_baseline_every_turn_is_still_reported(self, tmp_path):
        """No freeze, no cut — the absolute figures must not shrink."""
        path = self._session(tmp_path)
        out = governor.report_for_session(path, tmp_path / "space", 120_000)
        assert "10 turns" in out and "earlier" not in out


class TestNoTurnsSinceTheFreeze:
    """Zero cost against a positive baseline is a 100% saving — the most
    flattering possible lie, and the one a fresh `govern on` would tell."""

    def _out(self, tmp_path):
        _write_session(tmp_path / "projects", "p", "old",
                       [(2, 1_000, 300_000, 500)] * 3)
        _freeze(tmp_path / "space", {"avg_context": 150_000, "requests": 900,
                                     "units_per_request": 20_000.0,
                                     "prefix": 40_000,
                                     "taken_at": "2026-09-01T00:00:00"})
        return governor.report_for_session(
            tmp_path / "projects" / "p" / "old.jsonl", tmp_path / "space",
            120_000)

    def test_it_claims_no_saving_at_all(self, tmp_path):
        out = self._out(tmp_path)
        assert "Saved" not in out and "%" not in out

    def test_it_says_why_there_is_nothing_to_compare(self, tmp_path):
        out = self._out(tmp_path)
        assert "No turns recorded since the baseline was frozen" in out
        assert "2026-09-01T00:00:00" in out, "name the moment, not just the fact"


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


def _stat_failing_on_a(exc: OSError):
    """Patch for ``Path.stat`` that fails for ``a.jsonl`` and nothing else."""
    real = Path.stat

    def failing(self, *a, **kw):
        if self.name == "a.jsonl":
            raise exc
        return real(self, *a, **kw)

    return failing


def test_a_transcript_rotated_away_mid_walk_does_not_sink_the_report(tmp_path):
    """The host writes and rotates these files while we read them.

    The injected error carries ENOENT because that is what the kernel raises;
    an errno-less OSError would exercise a case no filesystem produces.
    """
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text("{}\n")
    (d / "b.jsonl").write_text("{}\n")
    gone = FileNotFoundError(errno.ENOENT, "No such file or directory", "a.jsonl")

    with mock.patch.object(Path, "stat", _stat_failing_on_a(gone)):
        got = governor._recent_transcripts(tmp_path / "projects", 10)
    assert [p.name for p in got] == ["b.jsonl"], \
        "the readable transcript survives; the vanished one is dropped, not fatal"


def test_an_unusual_errno_costs_one_transcript_and_not_the_report(tmp_path):
    """ESTALE over an NFS home used to escape the filter and empty the report.

    ``Path.is_file`` swallows only ENOENT/ENOTDIR/EBADF/ELOOP before 3.13 and
    re-raises everything else, which the caller could only catch by discarding
    every transcript it had already found.
    """
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text("{}\n")
    (d / "b.jsonl").write_text("{}\n")
    stale = OSError(errno.ESTALE, "Stale file handle", "a.jsonl")

    with mock.patch.object(Path, "stat", _stat_failing_on_a(stale)):
        got = governor._recent_transcripts(tmp_path / "projects", 10)
    assert [p.name for p in got] == ["b.jsonl"], \
        "one unreadable transcript must not cost the report"


def test_a_directory_named_like_a_transcript_is_not_a_transcript(tmp_path):
    """The glob matches names; only the stat says what the entry is."""
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    (d / "real.jsonl").write_text("{}\n")
    (d / "decoy.jsonl").mkdir()
    got = governor._recent_transcripts(tmp_path / "projects", 10)
    assert [p.name for p in got] == ["real.jsonl"]


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


# ── v3.9.4: status must report the obs.db that is actually written ──────
#
# `govern status` read obs.db from the CLI's own default storage. The capture
# hook writes into the space it was bound to at install time, a different
# directory entirely — so status described a database nothing writes to and
# printed 0.0 MB while 1.3 MB of observations sat in the real one. Reading that
# as "capture is dead" is the correct reading of the number, and it was wrong.


def _bind_capture(tmp_path, storage, *, obsstore_exists=True):
    """Write the sidecar a real install writes, under CLAUDE_DIR."""
    hooks = tmp_path / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    obsstore = hooks / "conscio_obsstore.py"
    if obsstore_exists:
        obsstore.write_text("# vendored\n", encoding="utf-8")
    storage.mkdir(parents=True, exist_ok=True)
    (hooks / "conscio_deepminer.json").write_text(json.dumps({
        "obsstore": str(obsstore), "storage": str(storage),
        "version": "3.9.4"}), encoding="utf-8")
    return storage


def _obs_of_size(space, megabytes):
    (space / "obs.db").write_bytes(b"\0" * int(megabytes * 1_048_576))


def test_status_reads_obs_db_from_the_bound_capture_space(
        tmp_path, monkeypatch, capsys):
    cli_space = _wire(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(cli_space.parent / "hermes"))
    cli_space.mkdir(parents=True, exist_ok=True)
    _obs_of_size(cli_space, 0.0)                       # the wrong one, empty
    capture = _bind_capture(tmp_path, tmp_path / "capture-space")
    _obs_of_size(capture, 2.0)                         # the one being written

    assert _cli()._cmd_govern("status", None, "", False) == 0
    out = capsys.readouterr().out
    assert "2.0 MB" in out
    assert "capture-space" in out
    assert "BROKEN" not in out


def test_status_names_the_repair_when_the_hook_cannot_record(
        tmp_path, monkeypatch, capsys):
    """The hook fails open, so a missing obsstore is indistinguishable from a
    quiet session. status is the only place that can say it out loud."""
    _wire(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    _bind_capture(tmp_path, tmp_path / "capture-space", obsstore_exists=False)

    assert _cli()._cmd_govern("status", None, "", False) == 0
    out = capsys.readouterr().out
    assert "BROKEN" in out and "conscio init --repair" in out


def test_an_explicit_storage_still_wins(tmp_path, monkeypatch, capsys):
    """Naming a path means that path — discovery is only for the default."""
    _wire(tmp_path, monkeypatch)
    _bind_capture(tmp_path, tmp_path / "capture-space")
    _obs_of_size(tmp_path / "capture-space", 2.0)
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    _obs_of_size(chosen, 3.0)

    assert _cli()._cmd_govern("status", None, str(chosen), False) == 0
    out = capsys.readouterr().out
    assert "3.0 MB" in out and "capture-space" not in out


def test_status_works_with_no_bundle_installed(tmp_path, monkeypatch, capsys):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    assert _cli()._cmd_govern("status", None, "", False) == 0
    out = capsys.readouterr().out
    assert "obs.db" in out and "BROKEN" not in out


# ── calibração contra fatura real (2026-08-06) ──────────────────────────────

def test_cache_write_weight_matches_the_one_hour_ttl_rate():
    """Claude Code cacheia no TTL de 1h, cobrado a 2x o input — não 1.25x.

    Conferido contra uso real: 48,4k in / 655,8k out / 124,4M read / 4,0M write
    fecha em $118,34 só a 2x; a 1,25x subestima em $15. O peso entra no termo de
    compactação, então 1,25 subestimava o custo de compactar em 60% e enviesava
    a recomendação para janelas pequenas demais.
    """
    assert governor.WEIGHTS["cw"] == 2.0
    assert governor.WEIGHTS["out"] / governor.WEIGHTS["in"] == 5.0   # $25 / $5
    assert governor.WEIGHTS["cr"] == 0.1                             # cache read


def test_modelled_cost_uses_the_landing_floor_not_the_prefix():
    """Uma compactação aterrissa em ``landed``, não em ``prefix``.

    O espaço de trabalho é o que sobra acima do ponto de aterrissagem. Usar o
    prefixo inflava esse espaço pela diferença inteira entre os dois (63k contra
    126k no host medido) e subestimava a frequência de compactação.
    """
    kw = {"prefix": 63_532, "requests": 14_071, "growth": 132.6,
          "out_per_request": 935.0}
    real = governor.modelled_cost(160_000, landed=125_586, **kw)
    otimista = governor.modelled_cost(160_000, **kw)      # cai de volta no prefixo
    assert real > otimista, "o piso real tem que custar mais que o otimista"


def test_measured_host_prefers_160k_over_the_thrashing_floor():
    """Regressão: com o piso certo o modelo para de recomendar 138k.

    138.144 passa do ``hard_floor``, mas deixa só 12,5k acima do ponto de
    aterrissagem — recompacta quase imediatamente. Antes da correção o modelo
    dizia que era 6,5% melhor que 160k.
    """
    kw = {"prefix": 63_532, "requests": 14_071, "growth": 132.6,
          "out_per_request": 935.0, "landed": 125_586}
    assert (governor.modelled_cost(160_000, **kw)
            < governor.modelled_cost(138_144, **kw))
