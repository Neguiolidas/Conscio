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
