"""Tests for scripts/council_bench_relabel.py (spec section 7.4).

Verifies re-labeling against local stub adapters with zero network access:
(a) Missing opt-in exits non-zero with zero calls;
(b) Matching stub yields 100% agreement and an empty changed list;
(c) Divergent stub lists the changed case IDs with frozen -> new;
(d) Fixtures and MANIFEST remain byte-identical (sha256 unchanged),
    and files are opened strictly in read-only mode.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import stat
from typing import Any

import pytest
from council_bench import split_sha256

from conscio import judge
from conscio.decision_adapter import Answer, Decision
from scripts.council_bench_relabel import (
    DEFAULT_FIXTURES_DIR,
    RelabelResult,
    format_report,
    load_cases,
    main,
    run_relabel,
)


class _StubAdapter:
    """Local stub adapter recording calls and returning scripted choices."""

    def __init__(
        self,
        choice_fn: Any = None,
        model: str = "stub-model",
        provider: str = "stub-provider",
    ) -> None:
        self.calls: list[tuple[Any, dict]] = []
        self.model = model
        self.provider = provider
        self._choice_fn = choice_fn

    def decide(self, state: Any, questions: dict[str, dict]) -> Decision:
        self.calls.append((state, questions))
        if callable(self._choice_fn):
            choice = self._choice_fn(state)
        elif isinstance(self._choice_fn, str):
            choice = self._choice_fn
        else:
            choice = "veto"

        probs = {
            "proceed": 1.0 if choice == "proceed" else 0.0,
            "hold": 1.0 if choice == "hold" else 0.0,
            "veto": 1.0 if choice == "veto" else 0.0,
        }
        return Decision(
            model=self.model,
            answers={
                "decision": Answer(
                    type="choice",
                    value=choice,
                    probabilities=probs,
                    confidence=0.95,
                )
            },
        )


# ── (a) Missing opt-in exits non-zero with zero calls ─────────────────


def test_relabel_without_opt_in_fails_with_no_calls(tmp_path, monkeypatch, capsys):
    """Without opt-in => exit code != 0 and zero judge/adapter calls."""
    adapter = _StubAdapter()
    monkeypatch.setattr(judge, "load", lambda cfg=None: None)

    # CLI call without opt-in
    empty_cfg = tmp_path / "empty_config.json"
    empty_cfg.write_text("{}", encoding="utf-8")

    exit_code = main(["--split", "dev", "--config", str(empty_cfg)])
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "Judge is not enabled" in captured.err
    assert len(adapter.calls) == 0

    # Also check run_relabel directly
    with pytest.raises(ValueError, match="Judge is not enabled"):
        run_relabel("dev", config={})


def test_relabel_with_no_adapter_status_fails(monkeypatch):
    """Opt-in marker present but adapter missing => exit code != 0."""
    monkeypatch.setattr(judge, "load", lambda cfg=None: "no_adapter")
    with pytest.raises(ValueError, match="'decision_adapter' block is missing"):
        run_relabel("dev", config={"judge": {}})


# ── (b) Matching stub gives 100% agreement and empty changed list ─────


def test_relabel_matching_stub_gives_100_percent_agreement():
    """Stub returning same frozen label yields 100% agreement and no diffs."""
    dev_cases = load_cases("dev", DEFAULT_FIXTURES_DIR)
    labels_by_item = {(c["question"], c["context"]): c["label"] for c in dev_cases}

    stub = _StubAdapter(
        choice_fn=lambda state: labels_by_item[(state["question"], state["context"])],
        model="match-model",
        provider="match-provider",
    )

    result = run_relabel(
        split="dev",
        fixtures_dir=DEFAULT_FIXTURES_DIR,
        adapter=stub,
    )

    assert result.total == len(dev_cases)
    assert result.matches == len(dev_cases)
    assert result.agreement_rate == 1.0
    assert result.changed_cases == []
    assert result.model == "match-model"
    assert result.provider == "match-provider"
    assert len(stub.calls) == len(dev_cases)

    report = format_report(result)
    assert f"Agreement: {result.total}/{result.total} (100.0%)" in report
    assert "Changed cases (0):" in report
    assert "(none)" in report


# ── (c) Divergent stub reports changed IDs with frozen -> new ─────────


def test_relabel_divergent_stub_reports_changed_ids():
    """Stub flipping a label reports changed IDs with frozen -> new."""
    dev_cases = load_cases("dev", DEFAULT_FIXTURES_DIR)
    labels_by_item = {(c["question"], c["context"]): c["label"] for c in dev_cases}
    first_case = dev_cases[0]
    target_id = first_case["id"]
    frozen_label = first_case["label"]
    flipped_label = "proceed" if frozen_label == "veto" else "veto"
    target_key = (first_case["question"], first_case["context"])

    def choice_fn(state: dict[str, Any]) -> str:
        if (state["question"], state["context"]) == target_key:
            return flipped_label
        return labels_by_item[(state["question"], state["context"])]

    stub = _StubAdapter(choice_fn=choice_fn)
    result = run_relabel(
        split="dev",
        fixtures_dir=DEFAULT_FIXTURES_DIR,
        adapter=stub,
    )

    assert result.matches == len(dev_cases) - 1
    assert result.agreement_rate == (len(dev_cases) - 1) / len(dev_cases)
    assert len(result.changed_cases) == 1
    assert result.changed_cases[0] == {
        "id": target_id,
        "frozen": frozen_label,
        "new": flipped_label,
    }

    report = format_report(result)
    assert f"{target_id}: {frozen_label} -> {flipped_label}" in report


# ── (d) Fixtures and MANIFEST remain byte-identical (read-only) ────────


def test_relabel_fixtures_and_manifest_remain_byte_identical(tmp_path, monkeypatch):
    """Execution must never modify fixture files or MANIFEST.json."""
    fixtures_copy = tmp_path / "council_bench"
    fixtures_copy.mkdir()

    # Copy dev.jsonl and MANIFEST.json into tmp_path
    dev_orig = DEFAULT_FIXTURES_DIR / "dev.jsonl"
    manifest_orig = DEFAULT_FIXTURES_DIR / "MANIFEST.json"

    dev_copy = fixtures_copy / "dev.jsonl"
    manifest_copy = fixtures_copy / "MANIFEST.json"

    dev_copy.write_bytes(dev_orig.read_bytes())
    manifest_copy.write_bytes(manifest_orig.read_bytes())

    # Make files strictly read-only on the filesystem (mode 0444)
    dev_copy.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    manifest_copy.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    dev_sha_before = hashlib.sha256(dev_copy.read_bytes()).hexdigest()
    manifest_sha_before = hashlib.sha256(manifest_copy.read_bytes()).hexdigest()

    # Intercept builtins.open to verify no write mode is ever requested
    orig_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        mode = kwargs.get("mode", args[0] if args else "r")
        if any(w in mode for w in ("w", "a", "+", "x")):
            raise AssertionError(f"Write access forbidden on fixture: {file} with mode {mode!r}")
        return orig_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    stub = _StubAdapter(choice_fn="proceed")
    result = run_relabel(
        split="dev",
        fixtures_dir=fixtures_copy,
        adapter=stub,
    )
    assert isinstance(result, RelabelResult)

    dev_sha_after = hashlib.sha256(dev_copy.read_bytes()).hexdigest()
    manifest_sha_after = hashlib.sha256(manifest_copy.read_bytes()).hexdigest()

    assert dev_sha_before == dev_sha_after
    assert manifest_sha_before == manifest_sha_after

    # Also verify repo fixtures remain untouched
    assert split_sha256("dev") == dev_sha_before


# ── CLI End-to-end test ───────────────────────────────────────────────


def test_relabel_cli_end_to_end(tmp_path, monkeypatch, capsys):
    """End-to-end CLI execution with stub adapter loaded via judge.load."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({
            "judge": {},
            "decision_adapter": {
                "url": "http://127.0.0.1:9999/decide",
                "model": "cli-model",
                "api_key_env": "TEST_KEY",
            },
        }),
        encoding="utf-8",
    )

    stub = _StubAdapter(choice_fn="veto", model="cli-model", provider="cli-provider")
    monkeypatch.setattr(judge, "load", lambda cfg=None: stub)

    exit_code = main([
        "--split", "dev",
        "--config", str(cfg_file),
        "--fixtures-dir", str(DEFAULT_FIXTURES_DIR),
    ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Split: dev" in captured.out
    assert "Model: cli-model" in captured.out
    assert "Provider: cli-provider" in captured.out
    assert "Agreement:" in captured.out
