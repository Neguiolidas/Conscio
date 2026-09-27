#!/usr/bin/env python3
"""Council benchmark re-labeling tool (spec 2026-09-26, section 7.4).

Re-labels fixture cases using a live decision judge (conscio.judge) via
the configured decision adapter to detect semantic drift against frozen labels.
Requires explicit opt-in (both 'judge' and 'decision_adapter' blocks).
Does not modify any fixture or manifest files (read-only).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conscio import adapter_config, judge
from conscio.judge import JudgeVerdict

DEFAULT_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "council_bench"
VALID_SPLITS = ("dev", "heldout")


@dataclass(frozen=True)
class RelabelResult:
    """Summary of re-labeling results and drift against frozen labels."""

    split: str
    total: int
    matches: int
    agreement_rate: float
    changed_cases: list[dict[str, str]]
    model: str
    provider: str


def load_cases(split: str, fixtures_dir: Path) -> list[dict[str, Any]]:
    """Load case records from the split jsonl file in read-only mode."""
    if split not in VALID_SPLITS:
        raise ValueError(f"Unknown split {split!r}; expected one of {VALID_SPLITS}")
    path = fixtures_dir / f"{split}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"Fixture split file not found: {path}")

    cases: list[dict[str, Any]] = []
    with open(path, mode="r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run_relabel(
    split: str = "dev",
    config: dict[str, Any] | None = None,
    fixtures_dir: Path | None = None,
    adapter: Any = None,
) -> RelabelResult:
    """Execute re-labeling against the judge and return drift statistics.

    If adapter is None, judge.load(config) is used to load the adapter,
    enforcing the opt-in requirement.
    """
    if fixtures_dir is None:
        fixtures_dir = DEFAULT_FIXTURES_DIR

    if adapter is None:
        loaded = judge.load(config)
        if loaded is None:
            raise ValueError(
                "Judge is not enabled. Both 'judge' and 'decision_adapter' blocks are required."
            )
        if isinstance(loaded, str):
            if loaded == "no_adapter":
                raise ValueError(
                    "Judge is configured but 'decision_adapter' block is missing."
                )
            if loaded == "no_key":
                raise ValueError(
                    "API key for decision adapter not found in environment, vault, or key file."
                )
            if loaded == "bad_config":
                raise ValueError(
                    "Invalid configuration for judge or decision adapter."
                )
            raise ValueError(f"Failed to load judge: {loaded}")
        adapter = loaded

    if not hasattr(adapter, "decide"):
        raise ValueError(f"Loaded object does not implement decide: {type(adapter).__name__}")

    cases = load_cases(split, fixtures_dir)
    total = len(cases)
    matches = 0
    changed_cases: list[dict[str, str]] = []
    models_seen: set[str] = set()
    providers_seen: set[str] = set()

    for case in cases:
        case_id = str(case["id"])
        frozen_label = str(case["label"])
        question = str(case["question"])
        context = str(case["context"])
        options = case.get("options")
        if options is not None and not isinstance(options, list):
            options = None

        verdict = judge.ask(adapter, question, context, options)
        if isinstance(verdict, str):
            raise RuntimeError(f"Judge error on case {case_id}: {verdict}")
        if not isinstance(verdict, JudgeVerdict):
            raise RuntimeError(
                f"Unexpected verdict type on case {case_id}: {type(verdict).__name__}"
            )

        new_label = verdict.choice
        adapter_model = getattr(adapter, "model", None)
        if verdict.model:
            models_seen.add(verdict.model)
        elif adapter_model:
            models_seen.add(str(adapter_model))

        verdict_provider = getattr(verdict, "provider", None)
        adapter_provider = getattr(adapter, "provider", None)
        provider = verdict_provider or adapter_provider
        if provider:
            providers_seen.add(str(provider))

        if new_label == frozen_label:
            matches += 1
        else:
            changed_cases.append({
                "id": case_id,
                "frozen": frozen_label,
                "new": new_label,
            })

    agreement_rate = (matches / total) if total > 0 else 1.0
    reported_model = ", ".join(sorted(models_seen)) if models_seen else str(getattr(adapter, "model", "n/a"))
    reported_provider = ", ".join(sorted(providers_seen)) if providers_seen else str(getattr(adapter, "provider", "n/a"))

    return RelabelResult(
        split=split,
        total=total,
        matches=matches,
        agreement_rate=agreement_rate,
        changed_cases=changed_cases,
        model=reported_model,
        provider=reported_provider,
    )


def format_report(result: RelabelResult) -> str:
    """Format the relabeling result into a human-readable drift report."""
    pct = result.agreement_rate * 100.0
    lines = [
        f"Split: {result.split}",
        f"Total cases: {result.total}",
        f"Agreement: {result.matches}/{result.total} ({pct:.1f}%)",
        f"Model: {result.model}",
        f"Provider: {result.provider}",
        f"Changed cases ({len(result.changed_cases)}):",
    ]
    if result.changed_cases:
        for item in result.changed_cases:
            lines.append(f"  {item['id']}: {item['frozen']} -> {item['new']}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for council_bench_relabel."""
    parser = argparse.ArgumentParser(
        description="Re-label council benchmark cases against a live judge to measure drift."
    )
    parser.add_argument(
        "--split",
        choices=["dev", "heldout"],
        default="dev",
        help="Split to re-label: dev or heldout (default: dev).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to config JSON file with judge and decision_adapter blocks.",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="Optional path to custom fixtures directory.",
    )
    args = parser.parse_args(argv)

    cfg: dict[str, Any] | None = None
    if args.config is not None:
        if not args.config.is_file():
            sys.stderr.write(f"Error: config file not found: {args.config}\n")
            return 1
        try:
            with open(args.config, mode="r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            sys.stderr.write(f"Error reading config JSON: {e}\n")
            return 1
    else:
        cfg = adapter_config.load_config()

    try:
        result = run_relabel(
            split=args.split,
            config=cfg,
            fixtures_dir=args.fixtures_dir,
        )
        print(format_report(result))
        return 0
    except ValueError as e:
        sys.stderr.write(f"Configuration error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Relabeling failed: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
