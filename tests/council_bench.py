"""Council calibration benchmark helpers (spec 2026-09-26, section 7.4).

Standard library only. Lives next to the tests, outside the package.

The frozen corpus in tests/fixtures/council_bench/ is read-only input for
the calibration round: this module loads it, and the harness built on it
prints aggregates and case ids only — never the question or context of a
heldout case (spec section 7.3, risk R-S5).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "council_bench"
SPLITS = ("dev", "heldout")
CLASSES = ("proceed", "hold", "veto")


def load_split(name: str) -> list[dict]:
    """Load a frozen split (dev|heldout) as a list of case records.

    Each record: {id, origin, question, context, options?, label,
    probabilities, confidence, model, provider, labeled_at}.
    """
    if name not in SPLITS:
        raise ValueError(f"unknown split {name!r}; expected one of {SPLITS}")
    path = FIXTURES_DIR / f"{name}.jsonl"
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for case in cases:
        if "id" not in case or case.get("label") not in CLASSES:
            raise ValueError(f"{name!r} corpus record without id/valid label")
    return cases


def load_manifest() -> dict:
    """The frozen manifest (hashes, counts, corpus_date)."""
    return json.loads((FIXTURES_DIR / "MANIFEST.json").read_text(encoding="utf-8"))


def split_sha256(name: str) -> str:
    """sha256 of the raw split file — the freeze check (spec section 7.3)."""
    if name not in SPLITS:
        raise ValueError(f"unknown split {name!r}; expected one of {SPLITS}")
    return hashlib.sha256((FIXTURES_DIR / f"{name}.jsonl").read_bytes()).hexdigest()


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa for two per-case class assignments, aligned by index.

    kappa = (p_o - p_e) / (1 - p_e), where p_o is the observed agreement
    rate and p_e the agreement expected from the marginal class
    distributions. A single-class sample (p_e == 1) is reported as 1.0 —
    the only observable outcome there is perfect agreement.
    """
    if len(a) != len(b):
        raise ValueError("kappa needs aligned assignments of equal length")
    n = len(a)
    if n == 0:
        raise ValueError("kappa is undefined for an empty sample")
    p_o = sum(1 for x, y in zip(a, b) if x == y) / n
    p_e = 0.0
    for cls in set(a) | set(b):
        p_e += (sum(1 for x in a if x == cls) / n) * (sum(1 for y in b if y == cls) / n)
    if p_e == 1.0:
        return 1.0
    return (p_o - p_e) / (1.0 - p_e)


def confusion(a: list[str], b: list[str]) -> dict[str, dict[str, int]]:
    """Confusion matrix over the two assignments: rows = a, columns = b.

    Zero-filled over the union of classes, so the printed shape is stable.
    """
    if len(a) != len(b):
        raise ValueError("confusion needs aligned pairs")
    classes = sorted(set(a) | set(b))
    matrix = {x: {y: 0 for y in classes} for x in classes}
    for x, y in zip(a, b):
        matrix[x][y] += 1
    return matrix


def strong_disagreements(cases: list[dict], preds: dict[str, str]) -> list[str]:
    """ids (R2) of cases where Jev was confident and the Council went opposite.

    A case qualifies when the frozen label's confidence >= 0.9 and the
    council verdict is the opposite class (proced<->veto, per spec R2).
    The harness prints only these ids; Hermet — the only one allowed to
    open the heldout — judges them without re-labeling.
    """
    out: list[str] = []
    for case in cases:
        if case.get("confidence", 0.0) < 0.9:
            continue
        council_class = preds.get(case["id"])
        if council_class is None:
            continue
        jev_class = case["label"]
        opposite = (jev_class == "proceed" and council_class == "veto") or (
            jev_class == "veto" and council_class == "proceed"
        )
        if opposite:
            out.append(case["id"])
    return sorted(out)
