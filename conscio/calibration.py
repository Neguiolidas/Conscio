"""v4.7 — ConfidenceValue and pure calibration metrics.

The calibration vocabulary of the framework. Every confidence-like number a
producer emits must carry its nature: `none` (no data — value is None, never
a fabricated prior), `asserted` (declared deterministic heuristic), `derived`
(posterior over observed data, e.g. Beta from the ledger), `measured`
(compared against ground truth with ECE/Brier and sample count).

stdlib-only. Pure functions. No I/O, no providers, no network.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Literal

MIN_CALIBRATION_SAMPLES = 5

ConfidenceCategory = Literal["none", "asserted", "derived", "measured"]

_ALLOWED_MEASURED_METRICS = frozenset({"ece", "brier", "accuracy"})


@dataclass(frozen=True)
class ConfidenceValue:
    """A confidence number with its nature attached.

    Invariants (frozen by the v4.7 spec):
    - ``none`` requires value None and metric None — absence is not a prior.
    - ``asserted``/``derived`` require a finite value in [0, 1].
    - ``measured`` requires samples >= MIN_CALIBRATION_SAMPLES and a known
      metric; below the minimum it is not a measurement.
    """

    value: float | None
    category: ConfidenceCategory
    samples: int
    metric: str | None = None
    lower_is_better: bool = False

    def __post_init__(self) -> None:
        if self.category == "none":
            if self.value is not None:
                raise ValueError(
                    "category 'none' requires value=None — a fabricated prior "
                    "is not absence of evidence")
            if self.metric is not None:
                raise ValueError(
                    "category 'none' cannot carry a metric")
            if self.lower_is_better:
                raise ValueError(
                    "category 'none' cannot have lower_is_better=True")
        else:
            if self.value is None:
                raise ValueError(
                    f"category {self.category!r} requires a numeric value")
            if not math.isfinite(self.value):
                raise ValueError("value must be finite")
            if not 0.0 <= self.value <= 1.0:
                raise ValueError(f"value must be in [0, 1], got {self.value}")
            if self.category == "measured":
                if self.samples < MIN_CALIBRATION_SAMPLES:
                    raise ValueError(
                        f"measured requires samples >= "
                        f"{MIN_CALIBRATION_SAMPLES}, got {self.samples}")
                if self.metric not in _ALLOWED_MEASURED_METRICS:
                    raise ValueError(
                        f"measured metric must be one of "
                        f"{sorted(_ALLOWED_MEASURED_METRICS)}, "
                        f"got {self.metric!r}")

    @classmethod
    def none(cls, samples: int = 0) -> ConfidenceValue:
        """Honest absence: no numeric claim, no fabricated prior."""
        return cls(category="none", value=None, samples=samples, lower_is_better=False)

    @classmethod
    def asserted(cls, value: float, samples: int = 0,
                 lower_is_better: bool = False) -> ConfidenceValue:
        return cls(category="asserted", value=value, samples=samples,
                   lower_is_better=lower_is_better)

    @classmethod
    def derived(cls, value: float, samples: int = 0,
                lower_is_better: bool = False) -> ConfidenceValue:
        return cls(category="derived", value=value, samples=samples,
                   lower_is_better=lower_is_better)

    @classmethod
    def measured(cls, value: float, samples: int, metric: str,
                 lower_is_better: bool | None = None) -> ConfidenceValue:
        if lower_is_better is None:
            lower_is_better = metric in ("ece", "brier")
        return cls(category="measured", value=value, samples=samples,
                   metric=metric, lower_is_better=lower_is_better)

    def to_json(self) -> str:
        return json.dumps({
            "category": self.category,
            "value": self.value,
            "samples": self.samples,
            "metric": self.metric,
            "lower_is_better": self.lower_is_better,
        }, ensure_ascii=False)

    def as_gate_input(self) -> float:
        """The ONLY way a ConfidenceValue enters a gate: raises unless the
        value is a real number. A `none` value has no decision weight — the
        caller must branch on the category instead of guessing."""
        if self.value is None:
            raise ValueError(
                f"category {self.category!r} carries no numeric value — "
                f"branch on the category, do not guess")
        return self.value


def _validate_pairs(confidence: list[float], outcomes: list[bool]) -> None:
    if not confidence or not outcomes:
        raise ValueError("empty inputs — no data, no metric")
    if len(confidence) != len(outcomes):
        raise ValueError(
            f"length mismatch: {len(confidence)} confidences vs "
            f"{len(outcomes)} outcomes")
    for c in confidence:
        if not math.isfinite(c) or not 0.0 <= c <= 1.0:
            raise ValueError(f"confidence out of [0, 1]: {c}")


def ece(confidence: list[float], outcomes: list[bool], bins: int = 5) -> float:
    """Expected Calibration Error over equal-width bins.

    ECE = sum over bins of (n_bin / N) * |mean_conf_bin - accuracy_bin|.
    Perfect calibration is 0.0; a confident-wrong agent scores high — the
    anti-correlated case that the old 1 - |E[C] - E[Y]| macro-distance
    rewarded as "perfectly calibrated" is exactly what this metric punishes.

    Outcomes must be binary booleans; ``partial`` records are excluded by the
    caller (MetaCognition passes resolved entries only).
    """
    _validate_pairs(confidence, outcomes)
    if bins < 1:
        raise ValueError("bins must be >= 1")
    n = len(confidence)
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [
            i for i, c in enumerate(confidence)
            if (lo <= c < hi) or (b == bins - 1 and c == 1.0)
        ]
        if not idx:
            continue
        mean_conf = sum(confidence[i] for i in idx) / len(idx)
        acc_bin = sum(1 for i in idx if outcomes[i]) / len(idx)
        total += (len(idx) / n) * abs(mean_conf - acc_bin)
    return total


def brier(confidence: list[float], outcomes: list[bool]) -> float:
    """Brier score: mean (confidence - outcome)^2 over binary outcomes.

    Range [0, 1]; 0 is perfect. Decomposes into reliability/resolution/
    uncertainty, but the mean form is what the calibration tiers report.
    """
    _validate_pairs(confidence, outcomes)
    return sum(
        (c - (1.0 if o else 0.0)) ** 2 for c, o in zip(confidence, outcomes)
    ) / len(confidence)
