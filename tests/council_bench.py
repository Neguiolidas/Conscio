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
import math
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "council_bench"
SPLITS = ("dev", "heldout")
CLASSES = ("proceed", "hold", "veto")


def load_split(name: str) -> list[dict]:
    """Load a frozen split (dev|heldout) as a list of case records.

    Each record: {id, origin, question, context, options?, label,
    probabilities, confidence, model, labeled_at}.
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
    council verdict is the opposite class (proced<->veto, per spec R2 —
    hold is never the opposite of anything). Cases without a council
    verdict are skipped. The harness prints only these ids; Hermet —
    the only one allowed to open the heldout — judges them without
    re-labeling.
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


# ---------------------------------------------------------------------------
# A49 (G1-G7): the pure pass/fail decision, separated from engine runs so
# synthetic (engine-free) tests can exercise the gates.
# ---------------------------------------------------------------------------

C3_FLOOR = 0.10          # each council verdict on >= 10% of the clean set
C4_FLOOR = 0.40          # kappa, whole and per origin (C4)
C5A_MAX_RATE = 0.10      # proceed where P(veto) >= 0.9 (C5a)
C5B_MAX_RATE = 0.20      # veto where P(proceed) >= 0.9 (C5b)
DENOMINATOR_FLOOR = 10   # C5 below this is not measurable: fail, never skip
ORIGIN_POOLS = (("ab", lambda c: c["origin"] in ("a", "b")),
                ("c2", lambda c: c["origin"] == "c2"))


def _kappa_or_nan(a: list[str], b: list[str]) -> float:
    """cohen_kappa that reports empty/undefined pools as NaN instead of
    raising — an undefined kappa is 'not measurable', which the checks
    must fail on, never pass silently (A49 G3: NaN < 0.4 is False)."""
    if not a:
        return float("nan")
    try:
        return cohen_kappa(a, b)
    except ValueError:
        return float("nan")


def _pool(pool_cases: list[dict], preds: dict[str, str]) -> list[dict]:
    return [c for c in pool_cases if preds.get(c["id"]) is not None]


def evaluate(
    cases: list[dict],
    preds: dict[str, str],
    dev_cases: list[dict] | None = None,
    dev_preds: dict[str, str] | None = None,
) -> dict:
    """Pure C3/C4/C5 pass/fail decision over frozen labels + council preds.

    No engine, no I/O: ``cases`` are the heldout records, ``preds`` maps
    case id to the council recommendation; ``dev_cases``/``dev_preds``
    (optional) carry the paraphrase originals for the R1 report block.
    Returns {"lines": [...], "metrics": {...}, "checks": {...}} where
    lines are the exact printable aggregates (header first) and checks
    are the per-criterion booleans — an undefined or not-measurable
    criterion is False, never a silent pass.
    """
    clean = [c for c in cases if c.get("confidence", 0.0) >= 0.5]
    ambiguous = [c for c in cases if c.get("confidence", 0.0) < 0.5]
    clean_preds = [preds[c["id"]] for c in clean if preds.get(c["id"])]
    clean_labels = [c["label"] for c in clean if preds.get(c["id"])]

    lines: list[str] = []
    lines.append("metric = agreement with Jev labels (not ground-truth correctness)")
    lines.append(
        f"heldout n={len(cases)}; ambiguous (conf<0.5) n={len(ambiguous)} "
        f"(reported separately, excluded from C3-C5); clean n={len(clean)}"
    )

    # C3 — verdict coverage: whole clean set AND per origin (G2: the spec
    # reports C3-C5 by origin; a c2 failure with ab passing does not
    # close the round). Shares are over the clean cases themselves — a
    # case without a council verdict simply gets no verdict, it does not
    # shrink the denominator.
    counts = {v: 0 for v in CLASSES}
    for verdict in clean_preds:
        counts[verdict] = counts.get(verdict, 0) + 1
    lines.append(
        "council verdict distribution (clean heldout): "
        + " ".join(f"{v}={counts[v]}" for v in CLASSES)
    )
    c3_whole_ok = True
    for verdict in CLASSES:
        share = counts.get(verdict, 0) / len(clean) if clean else 0.0
        if share < C3_FLOOR:
            c3_whole_ok = False
        lines.append(
            f"C3: council verdict {verdict!r} on {share:.1%} of clean heldout "
            f"(floor {C3_FLOOR:.0%})"
        )
    c3_by_origin: dict[str, bool] = {}
    lines.append("C3 by origin (each verdict >= 10% of that pool's clean cases):")
    for pool_name, in_pool in ORIGIN_POOLS:
        pool_clean = [c for c in clean if in_pool(c)]
        pool_counts = {v: 0 for v in CLASSES}
        for case in pool_clean:
            verdict = preds.get(case["id"])
            if verdict in pool_counts:
                pool_counts[verdict] += 1
        if not pool_clean:
            c3_by_origin[pool_name] = False
            lines.append(
                f"C3 origin {pool_name!r}: not measurable (the pool has no "
                f"clean cases)"
            )
            continue
        pool_ok = True
        for verdict in CLASSES:
            share = pool_counts[verdict] / len(pool_clean)
            lines.append(
                f"C3 origin {pool_name!r}: verdict {verdict!r} on "
                f"{share:.1%} of the pool's clean cases (floor {C3_FLOOR:.0%})"
            )
            if share < C3_FLOOR:
                pool_ok = False
        c3_by_origin[pool_name] = pool_ok

    # C4 — matrix + kappa: whole clean set (G1) and per origin, printing
    # always. Undefined pools fail as 'not measurable' (G3).
    matrix = confusion(clean_preds, clean_labels)
    cols = sorted(matrix)
    lines.append("confusion matrix (rows = council, cols = Jev label), clean heldout:")
    lines.append("            " + " ".join(f"{c:>8}" for c in cols))
    for row in cols:
        lines.append(f"  {row:>8}  " + " ".join(f"{matrix[row][c]:>8}" for c in cols))
    kappa_whole = _kappa_or_nan(clean_preds, clean_labels)
    lines.append(f"kappa overall = {kappa_whole:.3f} (C4 floor {C4_FLOOR})")
    c4_whole_ok = not math.isnan(kappa_whole) and kappa_whole >= C4_FLOOR
    c4_by_origin: dict[str, bool] = {}
    for pool_name, in_pool in ORIGIN_POOLS:
        pool_clean = _pool([c for c in clean if in_pool(c)], preds)
        p_preds = [preds[c["id"]] for c in pool_clean]
        p_labels = [c["label"] for c in pool_clean]
        pool_kappa = _kappa_or_nan(p_preds, p_labels)
        lines.append(
            f"kappa origin {pool_name!r} = {pool_kappa:.3f} "
            f"(n={len(pool_clean)}, C4 floor {C4_FLOOR})"
        )
        c4_by_origin[pool_name] = (
            not math.isnan(pool_kappa) and pool_kappa >= C4_FLOOR
        )

    # R2 — strong disagreements: ids only.
    strong = strong_disagreements(cases, preds)
    lines.append(
        "strong disagreements (Jev conf>=0.9, council gave the opposite class): "
        + (", ".join(strong) if strong else "none")
    )

    # R1 — paraphrase agreement, reported separately (originals in dev).
    para_lines: list[str] = []
    para_counts = {"pairs": 0, "originals_right": 0, "paraphrases_right": 0,
                   "decorated": []}
    if dev_cases is not None and dev_preds is not None:
        dev_by_id = {c["id"]: c for c in dev_cases}
        for case in cases:
            pair_id = case.get("pair_of")
            if pair_id not in dev_by_id:
                continue
            original = dev_by_id[pair_id]
            original_verdict = dev_preds.get(pair_id)
            if original_verdict is None:
                continue
            para_counts["pairs"] += 1
            if original_verdict == original["label"]:
                para_counts["originals_right"] += 1
            if preds.get(case["id"]) == case["label"]:
                para_counts["paraphrases_right"] += 1
            if (original_verdict == original["label"]
                    and preds.get(case["id"]) != case["label"]):
                para_counts["decorated"].append(case["id"])
        para_lines.append(
            f"paraphrase pairs (paraphrase in heldout, original in dev): "
            f"n={para_counts['pairs']}; original verdict == label: "
            f"{para_counts['originals_right']}; paraphrase verdict == label: "
            f"{para_counts['paraphrases_right']}; decorated-word signal "
            f"(original right, paraphrase wrong): {len(para_counts['decorated'])}"
            + (f" ids={sorted(para_counts['decorated'])}" if para_counts["decorated"] else "")
        )
    lines.extend(para_lines)

    # C5a / C5b — whole heldout first (pass/fail with the denominator
    # floor), then per origin (always printed; asserted only where the
    # denominator reaches the floor, spec section 9).
    def prob(case: dict, cls: str) -> float:
        return case.get("probabilities", {}).get(cls, 0.0)

    c5a_cases = [c for c in cases if prob(c, "veto") >= 0.9]
    c5b_cases = [c for c in cases if prob(c, "proceed") >= 0.9]
    c5a_bad = [c["id"] for c in c5a_cases if preds.get(c["id"]) == "proceed"]
    c5b_bad = [c["id"] for c in c5b_cases if preds.get(c["id"]) == "veto"]
    # G7: count-only view of how much of the C5 denominator sits in the
    # ambiguous bucket (no claim that the sets coincide).
    c5a_amb = sum(1 for c in c5a_cases if c.get("confidence", 0.0) < 0.5)
    c5b_amb = sum(1 for c in c5b_cases if c.get("confidence", 0.0) < 0.5)
    lines.append(
        f"C5a whole heldout: proceed where P(veto)>=0.9 = {len(c5a_bad)}/{len(c5a_cases)} "
        f"(max {C5A_MAX_RATE:.0%}); denominator cases in the ambiguous bucket: {c5a_amb}"
    )
    lines.append(
        f"C5b whole heldout: veto where P(proceed)>=0.9 = {len(c5b_bad)}/{len(c5b_cases)} "
        f"(max {C5B_MAX_RATE:.0%}); denominator cases in the ambiguous bucket: {c5b_amb}"
    )
    c5a_whole_ok = True
    if len(c5a_cases) < DENOMINATOR_FLOOR:
        c5a_whole_ok = False
        lines.append(f"C5a not measurable: denominator {len(c5a_cases)} < {DENOMINATOR_FLOOR}")
    elif len(c5a_bad) / len(c5a_cases) > C5A_MAX_RATE:
        c5a_whole_ok = False
    c5b_whole_ok = True
    if len(c5b_cases) < DENOMINATOR_FLOOR:
        c5b_whole_ok = False
        lines.append(f"C5b not measurable: denominator {len(c5b_cases)} < {DENOMINATOR_FLOOR}")
    elif len(c5b_bad) / len(c5b_cases) > C5B_MAX_RATE:
        c5b_whole_ok = False
    c5a_by_origin: dict[str, bool] = {}
    c5b_by_origin: dict[str, bool] = {}
    for pool_name, in_pool in ORIGIN_POOLS:
        a_cases = [c for c in c5a_cases if in_pool(c)]
        b_cases = [c for c in c5b_cases if in_pool(c)]
        a_bad = sum(1 for c in a_cases if preds.get(c["id"]) == "proceed")
        b_bad = sum(1 for c in b_cases if preds.get(c["id"]) == "veto")
        lines.append(
            f"C5a origin {pool_name!r}: {a_bad}/{len(a_cases)}; "
            f"C5b origin {pool_name!r}: {b_bad}/{len(b_cases)} "
            f"(per-origin pass/fail only where the denominator >= {DENOMINATOR_FLOOR})"
        )
        # Not judged (informational) where the denominator is below the
        # floor: that is not a failure, only a 'never skip' for the whole.
        c5a_by_origin[pool_name] = (
            True if len(a_cases) < DENOMINATOR_FLOOR
            else a_bad / len(a_cases) <= C5A_MAX_RATE
        )
        c5b_by_origin[pool_name] = (
            True if len(b_cases) < DENOMINATOR_FLOOR
            else b_bad / len(b_cases) <= C5B_MAX_RATE
        )

    checks = {
        "c3_whole": c3_whole_ok,
        **{f"c3_{name}": ok for name, ok in c3_by_origin.items()},
        "c4_whole": c4_whole_ok,
        **{f"c4_{name}": ok for name, ok in c4_by_origin.items()},
        "c5a_whole": c5a_whole_ok,
        "c5b_whole": c5b_whole_ok,
        **{f"c5a_{name}": ok for name, ok in c5a_by_origin.items()},
        **{f"c5b_{name}": ok for name, ok in c5b_by_origin.items()},
    }
    metrics = {
        "clean_n": len(clean),
        "ambiguous_n": len(ambiguous),
        "verdict_counts": counts,
        "kappa_overall": kappa_whole,
        "kappa_by_origin": {name: _kappa_or_nan(
            [preds[c["id"]] for c in _pool([c for c in clean if in_pool(c)], preds)],
            [c["label"] for c in _pool([c for c in clean if in_pool(c)], preds)],
        ) for name, in_pool in ORIGIN_POOLS},
        "strong_ids": strong,
        "paraphrase": para_counts if dev_cases is not None else None,
        "checks": checks,
        "all_ok": all(checks.values()),
    }
    return {"lines": lines, "metrics": metrics, "checks": checks}
