"""v3.9.2 Governor — measurement and reporting over host transcripts."""
import json

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
