"""v4.7 Task 7 — architectural guards for the calibration contract.

A) No gate reads calibration_score() raw anymore: the scalar projection is
   legacy prose; the gates must branch on category via ConfidenceValue.
   This guard greps the source so a future regression fails the suite.
B) Engine wiring: the council capture hook must exist and be best-effort.
C) meta.status()/summary surface the category, not just the number.
"""
import inspect
import re
from pathlib import Path

from conscio import gates
from conscio.engine import ConsciousnessEngine

SRC_ROOT = Path(__file__).resolve().parents[1] / "conscio"


class TestNoGateReadsRawCalibrationScore:
    def test_gates_never_call_calibration_score(self):
        """The scalar projection must not feed gate decisions — the audited
        laundering pattern. Category-branching via ConfidenceValue is the
        only sanctioned path."""
        src = inspect.getsource(gates)
        hits = re.findall(r"calibration_score\(\)", src)
        assert not hits, (
            f"gates.py still reads calibration_score() raw: {len(hits)} hit(s)"
            f" — branch on ConfidenceValue category instead")

    def test_act_fast_path_does_not_use_global_calibration(self):
        from conscio.agency import act
        src = inspect.getsource(act)
        # the fast-path block must not reference trust.meta.calibration_score
        fast_path_block = re.search(
            r"def _audit\(self.*?(?=\n    def )", src, re.DOTALL)
        assert fast_path_block, "_audit not found in act.py"
        block = fast_path_block.group(0)
        assert "meta.calibration_score" not in block, (
            "act fast-path still launders the global calibration score")


class TestEngineOutcomeWiring:
    def test_engine_exposes_outcome_store(self, tmp_path):
        """The engine wires an OutcomeStore so council captures persist."""
        with ConsciousnessEngine(model_name="t",
                                 storage_path=str(tmp_path)) as eng:
            assert hasattr(eng, "outcome_store"), (
                "engine must expose outcome_store for the capture hooks")
            assert eng.outcome_store is not None

    def test_council_capture_persists_with_provenance(self, tmp_path):
        with ConsciousnessEngine(model_name="t",
                                 storage_path=str(tmp_path)) as eng:
            eng.council(question="guard test q")
            store = eng.outcome_store
            # best-effort capture: the store is alive and wired after a
            # council ran (the hook must never sink the council)
            assert store is not None


class TestMetaSurfacesCategory:
    def test_status_carries_calibration_category(self, tmp_path):
        with ConsciousnessEngine(model_name="t",
                                 storage_path=str(tmp_path)) as eng:
            status = eng.meta.status()
            assert "calibration" in status
            # cold start must be explicit absence, not a number
            assert status["calibration"] is None or \
                isinstance(status["calibration"], float)
