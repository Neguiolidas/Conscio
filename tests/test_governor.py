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
