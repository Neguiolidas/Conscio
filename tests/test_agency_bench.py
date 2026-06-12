# tests/test_agency_bench.py
"""Bench CLI — offline with the mock adapter (spec section 10)."""
import json

import pytest

from conscio.bench import build_adapter, main, run_bench


class TestRunBench:
    def test_mock_bench_full_report(self, tmp_path):
        adapter = build_adapter("mock", cycles=4)
        report = run_bench(adapter, cycles=4, workdir=tmp_path)
        assert report["syntactic_validity"] == 1.0
        assert report["deterministic_catch_rate"] == 1.0
        assert report["semantic_catch_rate"] == 1.0
        assert report["catch_rate_total"] == 1.0
        assert report["tier"] == "T2"
        assert report["skeptic_mode"] == "open"
        assert report["profile"]["json_fidelity"] == 1.0
        assert report["latency_p50_ms"] >= 0
        assert 0.0 <= report["calibration"] <= 1.0
        assert report["llm_calls"] == 5 + 4 + 5   # probes + cycles + audits

    def test_unknown_adapter_spec_exits(self):
        with pytest.raises(SystemExit):
            build_adapter("warp-drive")

    def test_adapter_specs_parse(self):
        from conscio.agency.adapters import (LlamaCppAdapter, OllamaAdapter,
                                             OpenAICompatAdapter)
        assert isinstance(build_adapter("ollama:hermes3"), OllamaAdapter)
        assert isinstance(build_adapter("llamacpp"), LlamaCppAdapter)
        openai = build_adapter("openai:qwen@http://localhost:9999/v1")
        assert isinstance(openai, OpenAICompatAdapter)
        assert openai.base_url == "http://localhost:9999/v1"
        assert openai.model == "qwen"


class TestSkillCurve:
    """A9: the curve must rise once Distill kicks in (machinery proof —
    real model curves require a real adapter)."""

    def test_reactive_script_yields_valid_json_on_few_shot(self):
        from conscio.bench import reactive_mock_script
        entries = reactive_mock_script(4)
        fn = entries[0]
        text = fn("...\nExamples of past successful actions:\n...")
        assert json.loads(text)["tool"] == "fs_read"

    def test_curve_rises_after_distill(self, tmp_path):
        from conscio.bench import run_skill_curve
        adapter = build_adapter("mock", skill_cycles=20)
        report = run_skill_curve(adapter, cycles=20, dream_every=10,
                                 workdir=tmp_path)
        curve = report["skills_curve"]
        assert len(curve) == 2
        assert curve[0]["validity"] < curve[1]["validity"]
        assert curve[1]["validity"] == 1.0
        assert curve[1]["skills_total"] >= 1
        assert curve[1]["exemplars_served"] > 0

    def test_without_distill_curve_stays_flat(self, tmp_path):
        from conscio.bench import run_skill_curve
        adapter = build_adapter("mock", skill_cycles=20)
        report = run_skill_curve(adapter, cycles=20, dream_every=999,
                                 workdir=tmp_path)
        curve = report["skills_curve"]
        assert len(curve) == 1                    # single partial flush
        assert curve[0]["skills_total"] == 0
        assert curve[0]["validity"] < 1.0

    def test_main_skills_flag_prints_curve(self, tmp_path, capsys):
        code = main(["--adapter", "mock", "--skills", "20",
                     "--workdir", str(tmp_path / "wd")])
        assert code == 0
        assert "skill" in capsys.readouterr().out.lower()


class TestMain:
    def test_main_prints_and_writes_json(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        code = main(["--adapter", "mock", "--cycles", "3",
                     "--workdir", str(tmp_path / "wd"),
                     "--json", str(out)])
        assert code == 0
        printed = capsys.readouterr().out
        assert "syntactic validity" in printed.lower()
        data = json.loads(out.read_text())
        assert data["cycles"] == 3
