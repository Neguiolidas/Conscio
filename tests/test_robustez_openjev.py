"""Tests for the openjev + hindsight half of robustez (v4.9 item 13)."""
from __future__ import annotations

import json

import pytest

from conscio.robustez import consolidation, decision, deltaops, logitjev, retractions


# --- decision (openjev types) -------------------------------------------


def test_choice_limits() -> None:
    with pytest.raises(ValueError):
        decision.Choice(options=("a",))
    with pytest.raises(ValueError):
        decision.Choice(options=tuple(f"o{i}" for i in range(300)))
    with pytest.raises(ValueError):
        decision.Choice(options=("a", "a"))
    assert decision.Choice(options=("a", "b")).validate() is not None


def test_score_limits() -> None:
    with pytest.raises(ValueError):
        decision.Score(levels=1)
    with pytest.raises(ValueError):
        decision.Score(levels=11)
    assert decision.Score(levels=5).validate() is not None


def test_confidence_from_probs() -> None:
    assert decision.confidence_from_probs([0.25] * 4) == 0.0
    assert decision.confidence_from_probs([1.0, 0.0]) == 1.0
    assert decision.confidence_from_probs([]) == 0.0
    # (0.5*2-1)/(2-1) = 0 for binary 50/50
    assert decision.confidence_from_probs([0.5, 0.5]) == 0.0
    # generalized K=3, max=0.8: (0.8*3-1)/2 = 0.7
    assert abs(decision.confidence_from_probs([0.8, 0.1, 0.1]) - 0.7) < 1e-9


def test_weighted_score() -> None:
    # score levels 1..5; probs favoring level 4
    assert abs(decision.weighted_score([0.0, 0.0, 0.0, 1.0, 0.0]) - 4.0) < 1e-9
    assert decision.weighted_score([]) == 0.0


def test_render_answer_shapes() -> None:
    noul = decision.render_answer("q?", "noul", [0.7])
    assert noul == {"question": "q?", "type": "noul", "noul": 0.7}
    choice = decision.render_answer("q?", "choice", [0.6, 0.4],
                                    options=["a", "b"])
    assert choice["confidence"] > 0 and choice["options"] == ["a", "b"]


def test_entropy_nats() -> None:
    r = decision.SystemOneRequest(state="aaaaaaaa")
    assert r.entropy_nats() == 0.0  # one symbol: no entropy
    r2 = decision.SystemOneRequest(state="ab")
    assert abs(r2.entropy_nats() - 1.0) < 1e-9  # 2 symbols 50/50: 1 bit


# --- logitjev -----------------------------------------------------------


class _FakeJev(logitjev.OpenAICompatJev):
    def __init__(self, top: list[dict]) -> None:
        super().__init__("http://x/v1", "m")
        self._top = top

    def _chat(self, payload: dict) -> dict:
        return {"choices": [{"logprobs": {"content": [
            {"token": "x", "logprob": -0.1, "top_logprobs": self._top}]}}]}


def test_logitjev_first_token_is_the_answer() -> None:
    q = decision.Choice(options=("proceed", "hold"))
    jev = _FakeJev([
        {"token": "proceed", "logprob": -0.05},
        {"token": "hold", "logprob": -3.0},
    ])
    answer = jev.ask(q, state="state text")
    assert answer["probabilities"][0] > answer["probabilities"][1]
    assert answer["confidence"] > 0.9


def test_logitjev_honesty_label_missing_raises() -> None:
    q = decision.Choice(options=("proceed", "hold"))
    jev = _FakeJev([{"token": "something_else", "logprob": -0.1}])
    with pytest.raises(logitjev.JevError):
        jev.ask(q, state="s")


def test_logitjev_noul_case_insensitive() -> None:
    q = decision.Noul(question_hint="is it fine?")
    jev = _FakeJev([
        {"token": "yes", "logprob": -0.02},  # lowercase: falls back
        {"token": "no", "logprob": -3.8},
    ])
    answer = jev.ask(q, state="s")
    assert answer["noul"] > 0.9


def test_logitjev_score_digits() -> None:
    q = decision.Score(levels=5)
    jev = _FakeJev([
        {"token": "4", "logprob": -0.08},
        {"token": "3", "logprob": -2.4},
    ])
    answer = jev.ask(q, state="s")
    assert answer["probabilities"][3] > answer["probabilities"][2]


# --- retractions (hindsight) ---------------------------------------------


def test_partition_retracted_on_lost_fact() -> None:
    docs = [{"based_on": ["world:1", "world:2"], "text": "d1"},
            {"based_on": ["world:3"], "text": "d2"}]
    valid, retracted = retractions.partition_retracted(docs, {"world:1", "world:2"})
    assert [d["text"] for d in valid] == ["d1"]
    assert [d["text"] for d in retracted] == ["d2"]


def test_unresolvable_is_not_retraction() -> None:
    """A broken link (unknown fact type) stays valid — N3."""
    docs = [{"based_on": ["ghost:9"], "text": "d"}]
    valid, retracted = retractions.partition_retracted(docs, set())
    assert valid == docs and retracted == []


def test_prune_based_on_keeps_unresolvable() -> None:
    doc = {"based_on": ["world:1", "ghost:9"], "text": "d"}
    pruned = retractions.prune_based_on(doc, {"world:1"})
    assert pruned["based_on"] == ["world:1", "ghost:9"]  # live kept + unresolvable intact
    assert doc["based_on"] == ["world:1", "ghost:9"]  # input not mutated


def test_prune_noop_when_all_live() -> None:
    doc = {"based_on": ["world:1"], "text": "d"}
    pruned = retractions.prune_based_on(doc, {"world:1"})
    assert pruned["based_on"] == ["world:1"]  # unchanged (same list)


# --- deltaops (hindsight) -------------------------------------------------


def _doc() -> dict:
    return {"sections": [
        {"section_id": "sec1", "name": "One", "blocks": [
            {"block_id": "b1", "text": "first"},
            {"block_id": "b2", "text": "second"},
        ]}]}


def test_deltaops_shape_layer_refuses_whole_batch() -> None:
    with pytest.raises(deltaops.DeltaOperationsInvalidError):
        deltaops.apply_ops(_doc(), [{"op": "nope", "section_id": "sec1"}])


def test_deltaops_append_and_replace() -> None:
    doc = _doc()
    out = deltaops.apply_ops(doc, [
        {"op": "append_block", "section_id": "sec1", "text": "third"},
        {"op": "replace_block", "section_id": "sec1", "block_id": "b1",
         "text": "first (edited)"},
    ])
    blocks = out["document"]["sections"][0]["blocks"]
    assert [b["text"] for b in blocks] == ["first (edited)", "second", "third"]
    assert len(out["applied"]) == 2 and out["skipped"] == []


def test_deltaops_unknown_block_skips_only_that_op() -> None:
    out = deltaops.apply_ops(_doc(), [
        {"op": "replace_block", "section_id": "sec1", "block_id": "nope",
         "text": "x"},
        {"op": "append_block", "section_id": "sec1", "text": "ok"},
    ])
    assert len(out["applied"]) == 1 and len(out["skipped"]) == 1


def test_deltaops_doc_never_mutated() -> None:
    doc = _doc()
    snapshot = json.dumps(doc, sort_keys=True)
    deltaops.apply_ops(doc, [
        {"op": "append_block", "section_id": "sec1", "text": "x"},
        {"op": "remove_block", "section_id": "sec1", "block_id": "b1"}])
    assert json.dumps(doc, sort_keys=True) == snapshot


def test_deltaops_block_ids_minted_here() -> None:
    out = deltaops.apply_ops(_doc(), [
        {"op": "append_block", "section_id": "sec1", "text": "unique text"}])
    new_id = out["document"]["sections"][0]["blocks"][-1]["block_id"]
    assert new_id not in ("", "x")  # the model's id is discarded; sha1 minted


# --- consolidation (hindsight) ---------------------------------------------


def test_nine_processing_rules() -> None:
    assert len(consolidation.PROCESSING_RULES) == 9
    prompt = consolidation.consolidation_prompt(mission="m")
    for rule in consolidation.PROCESSING_RULES:
        assert rule in prompt


def test_directives_render_active_by_priority() -> None:
    ds = [
        consolidation.Directive(name="a", content="A", priority=1),
        consolidation.Directive(name="b", content="B", priority=5),
        consolidation.Directive(name="c", content="C", priority=9,
                                is_active=False),
    ]
    out = consolidation.render_directives(ds)
    assert out.splitlines()[0].startswith("b")
    assert "C" not in out  # inactive never renders


def test_directive_tag_filter() -> None:
    ds = [consolidation.Directive(name="x", content="X", priority=1, tags=["t1"])]
    assert "X" in consolidation.render_directives(ds, tag="t1")
    assert consolidation.render_directives(ds, tag="other") == ""
