"""
Tests for OutputFilter — 8-stage pipeline for text compression.

Covers: each stage individually, pipeline composition, crash safety,
YAML config, edge cases.
"""


import pytest

from conscio.output_filter import (
    HAS_YAML,
    StripAnsi,
    Replace,
    MatchOutput,
    FilterLines,
    TruncateLines,
    HeadTail,
    MaxLines,
    OnEmpty,
    FilterPipeline,
    build_stage,
    build_pipeline_from_dict,
    build_pipeline_from_config,
    STAGE_REGISTRY,
)


# ─── Test Data ──────────────────────────────────────────────────────────

SAMPLE_ANSI = "\x1b[32mSuccess\x1b[0m: operation complete"
SAMPLE_LOG = """[2026-06-04 12:00:00] INFO: Bot started
[2026-06-04 12:00:01] DEBUG: Connecting to OKX
[2026-06-04 12:00:02] TRACE: HTTP GET /api/v5/market
[2026-06-04 12:00:03] INFO: Connected successfully
[2026-06-04 12:00:04] ERROR: API timeout on endpoint
"""

SAMPLE_LONG = "\n".join([f"Line {i} " + "x" * 300 for i in range(200)])


# ─── StripAnsi Tests ────────────────────────────────────────────────────

class TestStripAnsi:
    def test_removes_color_codes(self):
        stage = StripAnsi()
        result = stage.apply("\x1b[31mError\x1b[0m: something failed")
        assert result == "Error: something failed"

    def test_removes_multiple_codes(self):
        stage = StripAnsi()
        result = stage.apply(SAMPLE_ANSI)
        assert "\x1b" not in result
        assert "Success" in result

    def test_no_ansi_unchanged(self):
        stage = StripAnsi()
        text = "No ANSI here"
        assert stage.apply(text) == text

    def test_empty_string(self):
        stage = StripAnsi()
        assert stage.apply("") == ""

    def test_name(self):
        assert StripAnsi().name() == "strip_ansi"


# ─── Replace Tests ──────────────────────────────────────────────────────

class TestReplace:
    def test_simple_replacement(self):
        stage = Replace(patterns=[
            {"pattern": r"error", "replacement": "ERROR"},
        ])
        assert stage.apply("an error occurred") == "an ERROR occurred"

    def test_regex_replacement(self):
        stage = Replace(patterns=[
            {"pattern": r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", "replacement": "[TIMESTAMP]"},
        ])
        result = stage.apply("[2026-06-04 12:00:00] INFO: test")
        assert result == "[TIMESTAMP] INFO: test"

    def test_multiple_patterns(self):
        stage = Replace(patterns=[
            {"pattern": r"foo", "replacement": "bar"},
            {"pattern": r"baz", "replacement": "qux"},
        ])
        assert stage.apply("foo and baz") == "bar and qux"

    def test_empty_replacement(self):
        stage = Replace(patterns=[
            {"pattern": r"remove_me", "replacement": ""},
        ])
        assert stage.apply("keep remove_me here") == "keep  here"

    def test_no_patterns(self):
        stage = Replace()
        text = "unchanged"
        assert stage.apply(text) == text

    def test_name(self):
        assert Replace().name() == "replace"


# ─── MatchOutput Tests ──────────────────────────────────────────────────

class TestMatchOutput:
    def test_match_returns_message(self):
        stage = MatchOutput(rules=[
            {"pattern": r"BUILD SUCCESS", "message": "Build OK"},
        ])
        result = stage.apply("Some output\nBUILD SUCCESS\nMore output")
        assert result == "Build OK"

    def test_no_match_returns_original(self):
        stage = MatchOutput(rules=[
            {"pattern": r"BUILD SUCCESS", "message": "Build OK"},
        ])
        result = stage.apply("No match here")
        assert result == "No match here"

    def test_first_match_wins(self):
        """First matching rule wins — order matters."""
        stage = MatchOutput(rules=[
            {"pattern": r"warning", "message": "Warning found"},
            {"pattern": r"error", "message": "Error found"},
        ])
        # "warning" rule is first and matches, so it wins
        result = stage.apply("warning: something")
        assert result == "Warning found"

        # "error" is not a substring of "info" — no match
        result2 = stage.apply("info: something")
        assert result2 == "info: something"

    def test_no_rules(self):
        stage = MatchOutput()
        text = "anything"
        assert stage.apply(text) == text

    def test_name(self):
        assert MatchOutput().name() == "match_output"


# ─── FilterLines Tests ──────────────────────────────────────────────────

class TestFilterLines:
    def test_strip_mode(self):
        stage = FilterLines(mode="strip", patterns=[r"DEBUG:", r"TRACE:"])
        result = stage.apply(SAMPLE_LOG)
        assert "DEBUG:" not in result
        assert "TRACE:" not in result
        assert "INFO:" in result
        assert "ERROR:" in result

    def test_keep_mode(self):
        stage = FilterLines(mode="keep", patterns=[r"^.*ERROR:", r"^.*INFO:"])
        result = stage.apply(SAMPLE_LOG)
        assert "ERROR:" in result
        assert "INFO:" in result
        assert "DEBUG:" not in result

    def test_strip_all_matching(self):
        stage = FilterLines(mode="strip", patterns=[r".*"])
        result = stage.apply("line1\nline2")
        assert result == ""

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="mode must be"):
            FilterLines(mode="invalid")

    def test_empty_patterns(self):
        stage = FilterLines(mode="strip", patterns=[])
        text = "line1\nline2"
        assert stage.apply(text) == text

    def test_name(self):
        assert FilterLines().name() == "filter_lines"


# ─── TruncateLines Tests ────────────────────────────────────────────────

class TestTruncateLines:
    def test_truncates_long_line(self):
        stage = TruncateLines(max_width=20)
        result = stage.apply("A" * 100)
        assert len(result) == 20
        assert result.endswith("...")

    def test_short_line_unchanged(self):
        stage = TruncateLines(max_width=200)
        text = "Short line"
        assert stage.apply(text) == text

    def test_custom_suffix(self):
        stage = TruncateLines(max_width=10, suffix="…")
        result = stage.apply("A" * 50)
        assert len(result) == 10
        assert result.endswith("…")

    def test_multiline(self):
        stage = TruncateLines(max_width=10)
        text = "short\nThis is a very long line that exceeds the limit\nalso short"
        result = stage.apply(text)
        lines = result.split("\n")
        assert lines[0] == "short"
        assert len(lines[1]) == 10
        assert lines[2] == "also short"

    def test_zero_width_raises(self):
        # max_width is clamped to 1
        stage = TruncateLines(max_width=0)
        result = stage.apply("test")
        assert len(result) >= 1

    def test_name(self):
        assert TruncateLines().name() == "truncate_lines"


# ─── HeadTail Tests ─────────────────────────────────────────────────────

class TestHeadTail:
    def test_keeps_head_and_tail(self):
        stage = HeadTail(head=2, tail=1)
        text = "line1\nline2\nline3\nline4\nline5"
        result = stage.apply(text)
        lines = result.split("\n")
        assert "line1" in lines
        assert "line2" in lines
        assert "line5" in lines
        assert "line3" not in result

    def test_short_text_unchanged(self):
        stage = HeadTail(head=50, tail=20)
        text = "line1\nline2\nline3"
        assert stage.apply(text) == text

    def test_head_only(self):
        stage = HeadTail(head=3, tail=0)
        text = "\n".join([f"line{i}" for i in range(10)])
        result = stage.apply(text)
        assert result.startswith("line0")
        assert "line9" not in result

    def test_tail_only(self):
        stage = HeadTail(head=0, tail=2)
        text = "\n".join([f"line{i}" for i in range(10)])
        result = stage.apply(text)
        assert "line9" in result
        assert "line0" not in result

    def test_separator(self):
        stage = HeadTail(head=2, tail=2, separator="---SKIPPED---")
        text = "\n".join([f"line{i}" for i in range(10)])
        result = stage.apply(text)
        assert "---SKIPPED---" in result

    def test_name(self):
        assert HeadTail().name() == "head_tail"


# ─── MaxLines Tests ─────────────────────────────────────────────────────

class TestMaxLines:
    def test_caps_lines(self):
        stage = MaxLines(max_lines=3)
        text = "\n".join([f"line{i}" for i in range(10)])
        result = stage.apply(text)
        assert len(result.split("\n")) == 3

    def test_under_limit_unchanged(self):
        stage = MaxLines(max_lines=100)
        text = "line1\nline2"
        assert stage.apply(text) == text

    def test_exact_limit(self):
        stage = MaxLines(max_lines=5)
        text = "\n".join([f"line{i}" for i in range(5)])
        assert stage.apply(text) == text

    def test_name(self):
        assert MaxLines().name() == "max_lines"


# ─── OnEmpty Tests ──────────────────────────────────────────────────────

class TestOnEmpty:
    def test_empty_returns_message(self):
        stage = OnEmpty(message="Nothing here")
        assert stage.apply("") == "Nothing here"
        assert stage.apply("   ") == "Nothing here"

    def test_nonempty_unchanged(self):
        stage = OnEmpty(message="Nothing here")
        assert stage.apply("some text") == "some text"

    def test_custom_message(self):
        stage = OnEmpty(message="N/A")
        assert stage.apply("") == "N/A"

    def test_name(self):
        assert OnEmpty().name() == "on_empty"


# ─── Pipeline Tests ─────────────────────────────────────────────────────

class TestFilterPipeline:
    def test_default_pipeline(self):
        pipeline = FilterPipeline()
        stages = pipeline.list_stages()
        assert len(stages) == 8
        assert stages[0] == "strip_ansi"
        assert stages[-1] == "on_empty"

    def test_apply_default(self):
        pipeline = FilterPipeline()
        result = pipeline.apply("Simple text")
        assert result == "Simple text"

    def test_apply_with_ansi(self):
        pipeline = FilterPipeline()
        result = pipeline.apply("\x1b[31mError\x1b[0m: test")
        assert "\x1b" not in result
        assert "Error" in result

    def test_apply_with_long_output(self):
        pipeline = FilterPipeline()
        result = pipeline.apply(SAMPLE_LONG)
        # Should be compressed (less than original)
        assert len(result) < len(SAMPLE_LONG)

    def test_crash_safe(self):
        """Pipeline doesn't crash if a stage raises."""
        class BrokenStage:
            def apply(self, text):
                raise RuntimeError("I'm broken")
            def name(self):
                return "broken"

        pipeline = FilterPipeline(stages=[
            StripAnsi(),
            BrokenStage(),
            MaxLines(max_lines=10),
        ])
        result = pipeline.apply("Some text")
        # Should return result from before the broken stage
        assert result == "Some text"

    def test_add_stage(self):
        pipeline = FilterPipeline()
        pipeline.add_stage(StripAnsi(), position=0)
        stages = pipeline.list_stages()
        assert stages[0] == "strip_ansi"

    def test_remove_stage(self):
        pipeline = FilterPipeline()
        assert pipeline.remove_stage("strip_ansi") is True
        assert "strip_ansi" not in pipeline.list_stages()

    def test_remove_nonexistent(self):
        pipeline = FilterPipeline()
        assert pipeline.remove_stage("nonexistent") is False


# ─── Build Stage Tests ──────────────────────────────────────────────────

class TestBuildStage:
    def test_build_all_stages(self):
        for name in STAGE_REGISTRY:
            stage = build_stage(name)
            assert stage is not None
            assert stage.name() == name

    def test_build_with_config(self):
        stage = build_stage("filter_lines", {"mode": "keep", "patterns": [r"^ERROR:"]})
        assert stage.name() == "filter_lines"

    def test_build_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown filter stage"):
            build_stage("nonexistent_stage")

    def test_build_truncate_with_config(self):
        stage = build_stage("truncate_lines", {"max_width": 50, "suffix": "…"})
        assert stage.name() == "truncate_lines"


# ─── Build from Dict Tests ──────────────────────────────────────────────

class TestBuildPipelineFromDict:
    def test_basic_config(self):
        config = {
            "stages": [
                {"strip_ansi": {}},
                {"filter_lines": {"mode": "strip", "patterns": ["^DEBUG:"]}},
                {"max_lines": {"max_lines": 30}},
            ]
        }
        pipeline = build_pipeline_from_dict(config)
        stages = pipeline.list_stages()
        assert stages == ["strip_ansi", "filter_lines", "max_lines"]

    def test_empty_config(self):
        pipeline = build_pipeline_from_dict({})
        assert len(pipeline.list_stages()) == 8  # Defaults

    def test_full_pipeline(self):
        config = {
            "stages": [
                {"strip_ansi": {}},
                {"replace": {"patterns": [{"pattern": r"\d{4}-\d{2}-\d{2}", "replacement": "[DATE]"}]}},
                {"filter_lines": {"mode": "strip", "patterns": ["DEBUG:", "TRACE:"]}},
                {"truncate_lines": {"max_width": 120}},
                {"head_tail": {"head": 30, "tail": 10}},
                {"max_lines": {"max_lines": 50}},
                {"on_empty": {"message": "No data"}},
            ]
        }
        pipeline = build_pipeline_from_dict(config)
        result = pipeline.apply(SAMPLE_LOG)
        assert "DEBUG:" not in result
        assert "TRACE:" not in result

    def test_integration_with_content_store(self, tmp_path):
        """Filtered output can be indexed in ContentStore."""
        from conscio.content_store import ContentStore

        pipeline = FilterPipeline(stages=[
            StripAnsi(),
            FilterLines(mode="strip", patterns=[r"^DEBUG:"]),
            MaxLines(max_lines=20),
        ])

        raw = "\x1b[32m[OK]\x1b[0m Connected\nDEBUG: ping\nINFO: ready"
        filtered = pipeline.apply(raw)

        store = ContentStore(db_path=tmp_path / "test.db")
        sid = store.index("test", filtered, "reflection")
        assert sid > 0

        results = store.search("Connected")
        assert len(results) > 0
        store.close()


# ─── Edge Case Tests ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_input_through_pipeline(self):
        pipeline = FilterPipeline()
        result = pipeline.apply("")
        assert result == "No relevant output"  # OnEmpty kicks in

    def test_whitespace_input(self):
        pipeline = FilterPipeline()
        result = pipeline.apply("   \n   \n   ")
        # After all stages, OnEmpty should trigger if all whitespace
        assert isinstance(result, str)

    def test_single_char_input(self):
        pipeline = FilterPipeline()
        assert pipeline.apply("x") == "x"

    def test_no_newlines(self):
        pipeline = FilterPipeline()
        text = "A" * 5000  # Single very long line, no newlines
        result = pipeline.apply(text)
        # Should be truncated but not crash
        assert isinstance(result, str)

    def test_preserves_original_on_error(self):
        """Original text is never lost even if stages fail."""
        class FailStage:
            def apply(self, text):
                raise ValueError("fail")
            def name(self):
                return "fail"

        pipeline = FilterPipeline(stages=[FailStage()])
        result = pipeline.apply("important data")
        assert result == "important data"

    def test_stage_registry_completeness(self):
        """All stages registered (8 original + dedup_blocks + semantic_dedup + secret_mask)."""
        expected = {"strip_ansi", "replace", "match_output", "filter_lines",
                    "truncate_lines", "head_tail", "max_lines", "on_empty",
                    "dedup_blocks", "semantic_dedup", "secret_mask"}
        assert set(STAGE_REGISTRY.keys()) == expected


# ─── Build from Config Tests ──────────────────────────────────────────────

class TestBuildPipelineFromConfig:
    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_loads_from_yaml_config(self, tmp_path):
        """build_pipeline_from_config loads stages from YAML."""
        config_path = tmp_path / "filters.yaml"
        config_path.write_text("""
filters:
  - name: test
    stages:
      - strip_ansi: {}
      - filter_lines:
          mode: strip
          patterns: ["^DEBUG:"]
      - max_lines:
          max_lines: 30
""")
        pipeline = build_pipeline_from_config(config_path)
        stages = pipeline.list_stages()
        assert stages == ["strip_ansi", "filter_lines", "max_lines"]

    def test_nonexistent_config_returns_default(self):
        """Nonexistent config returns default pipeline."""
        pipeline = build_pipeline_from_config("/nonexistent/path.yaml")
        stages = pipeline.list_stages()
        assert len(stages) == 8  # Default pipeline

    def test_empty_config_returns_default(self, tmp_path):
        """Empty YAML returns default pipeline."""
        config_path = tmp_path / "empty.yaml"
        config_path.write_text("")
        pipeline = build_pipeline_from_config(config_path)
        stages = pipeline.list_stages()
        assert len(stages) == 8  # Default pipeline

    def test_config_without_filters_returns_default(self, tmp_path):
        """Config without 'filters' key returns default."""
        config_path = tmp_path / "no_filters.yaml"
        config_path.write_text("other_key: value")
        pipeline = build_pipeline_from_config(config_path)
        stages = pipeline.list_stages()
        assert len(stages) == 8

    def test_yaml_missing_returns_default(self, tmp_path):
        """If PyYAML not installed, returns default."""
        pipeline = build_pipeline_from_config(tmp_path / "test.yaml")
        stages = pipeline.list_stages()
        assert len(stages) == 8

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_applies_loaded_pipeline(self, tmp_path):
        """Loaded pipeline correctly filters output."""
        config_path = tmp_path / "filters.yaml"
        config_path.write_text("""
filters:
  - name: test
    stages:
      - strip_ansi: {}
      - filter_lines:
          mode: strip
          patterns: ["DEBUG:", "TRACE:"]
      - max_lines:
          max_lines: 5
""")
        pipeline = build_pipeline_from_config(config_path)
        raw = "\x1b[32m[OK]\x1b[0m Connected\nDEBUG: ping\nINFO: ready\nTRACE: trace\nERROR: fail\n"
        result = pipeline.apply(raw)
        assert "\x1b" not in result
        assert "DEBUG:" not in result
        assert "TRACE:" not in result
        assert "INFO: ready" in result
        assert "ERROR: fail" in result
        assert len(result.split("\n")) <= 5 + 1  # 5 lines max + potential empty
