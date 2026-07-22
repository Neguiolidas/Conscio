"""TDD: Evaluate output_quality axis (LLM-as-judge)."""
import tempfile
from conscio import ConsciousnessEngine
from conscio.evaluation import evaluate


def test_evaluate_without_output():
    """Without output, output_quality is None or absent."""
    with tempfile.TemporaryDirectory() as d:
        eng = ConsciousnessEngine(model_name="t", storage_path=d)
        result = evaluate(eng, task_description="test")
        oq = [a for a in result.axes if a.axis == "output_quality"]
        # Either absent or None
        if oq:
            assert oq[0].score is None
        eng.close()


def test_evaluate_with_output_no_adapter():
    """With output but no adapter, output_quality uses heuristic scoring."""
    with tempfile.TemporaryDirectory() as d:
        eng = ConsciousnessEngine(model_name="t", storage_path=d)
        # Use longer output so heuristic doesn't score it "too short"
        result = evaluate(eng, task_description="test", output="Some text output that is longer than five words to test heuristic scoring properly")
        oq = [a for a in result.axes if a.axis == "output_quality"]
        assert len(oq) == 1
        assert 1 <= oq[0].score <= 5
        eng.close()


def test_evaluate_output_quality_axis_present():
    """output_quality appears when output is provided."""
    with tempfile.TemporaryDirectory() as d:
        eng = ConsciousnessEngine(model_name="t", storage_path=d)
        result = evaluate(eng, task_description="test", output="Test output")
        axes_names = [a.axis for a in result.axes]
        assert "output_quality" in axes_names
        eng.close()


def test_evaluate_still_has_original_5_axes():
    """Original 5 axes still present."""
    with tempfile.TemporaryDirectory() as d:
        eng = ConsciousnessEngine(model_name="t", storage_path=d)
        result = evaluate(eng, task_description="test")
        axes_names = [a.axis for a in result.axes]
        for expected in ["accuracy", "completeness", "clarity", "actionability", "conciseness"]:
            assert expected in axes_names
        eng.close()


def test_evaluate_output_quality_has_rationale():
    """output_quality axis includes evidence field."""
    with tempfile.TemporaryDirectory() as d:
        eng = ConsciousnessEngine(model_name="t", storage_path=d)
        result = evaluate(eng, task_description="test", output="Test output")
        oq = [a for a in result.axes if a.axis == "output_quality"][0]
        assert oq.evidence is not None
        eng.close()


def test_evaluate_output_quality_score_range():
    """Score must be 1-5 (matching evaluation.py scale)."""
    with tempfile.TemporaryDirectory() as d:
        eng = ConsciousnessEngine(model_name="t", storage_path=d)
        result = evaluate(eng, task_description="test", output="Test output")
        oq = [a for a in result.axes if a.axis == "output_quality"][0]
        assert 1 <= oq.score <= 5
        eng.close()
