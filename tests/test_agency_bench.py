# tests/test_agency_bench.py
"""Bench CLI — offline with the mock adapter (spec section 10)."""
import json

import pytest

from conscio import bench as bench_mod
from conscio.agency.adapter import (
    AdapterCaps,
    AdapterConnectionError,
    AdapterError,
    InferenceAdapter,
    InferenceResult,
)
from conscio.bench import build_adapter, main, run_bench, run_skill_curve


class _DeadAdapter(InferenceAdapter):
    """Every generate() raises — simulates an unreachable backend."""

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None):
        raise AdapterConnectionError("connection refused")

    def capabilities(self):
        return AdapterCaps(model_name="dead", json_mode=False, grammar=False)


class _DiesAfter(InferenceAdapter):
    """Valid for the first n generate() calls, then the backend dies."""

    def __init__(self, n):
        self.n = n
        self.calls = 0

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None):
        self.calls += 1
        if self.calls > self.n:
            raise AdapterConnectionError("backend died")
        return InferenceResult(
            text='{"tool": "fs_read", "args": {"path": "notes.md"},'
                 ' "rationale": "r", "expected_outcome": "o"}',
            tokens_in=1, tokens_out=1, latency_ms=0)

    def capabilities(self):
        return AdapterCaps(model_name="dies", json_mode=True, grammar=False)


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


class TestSkillCurveCrashSafe:
    """v1.2: a real curve run on CPU is long — write after every bucket so a
    crash leaves a usable partial file, tagged complete vs aborted."""

    def test_skill_curve_writes_incrementally(self, tmp_path):
        out = tmp_path / "curve.json"
        report = run_skill_curve(
            build_adapter("mock", skill_cycles=20), cycles=20,
            dream_every=10, workdir=tmp_path / "wd", json_path=out)
        assert report["status"] == "complete"
        data = json.loads(out.read_text())
        assert data["status"] == "complete"
        assert data["skills_curve"]                # buckets present

    def test_skill_curve_marks_partial_on_backend_death(self, tmp_path):
        out = tmp_path / "curve.json"
        report = run_skill_curve(_DiesAfter(5), cycles=40, dream_every=5,
                                 workdir=tmp_path / "wd", json_path=out)
        assert report["status"] == "aborted"
        assert report["error"]                     # carries the cause
        assert report["skills_curve"]              # the bucket before death
        data = json.loads(out.read_text())
        assert data["status"] == "aborted"


class TestBackendDown:
    def test_run_bench_raises_when_backend_unreachable(self, tmp_path):
        with pytest.raises(AdapterError):
            run_bench(_DeadAdapter(), cycles=1, workdir=tmp_path)

    def test_main_handles_backend_down(self, monkeypatch, capsys):
        monkeypatch.setattr(bench_mod, "build_adapter",
                            lambda *a, **k: _DeadAdapter())
        rc = bench_mod.main(["--adapter", "ollama:whatever", "--cycles", "1"])
        out = capsys.readouterr().out
        assert rc == 2
        assert "backend" in out.lower()


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
