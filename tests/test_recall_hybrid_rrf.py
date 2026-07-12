"""Calibrated hybrid recall: RRF fusion of lexical (BM25) and dense (RAG).

Before: recall() filled the first k slots with lexical hits, then appended
semantic hits — so with a full lexical page the dense results NEVER appeared,
and a strong semantic match could not outrank a weak lexical one. Now the two
rankings are fused with Reciprocal Rank Fusion (the same family already used to
merge the porter/trigram FTS indexes), so cross-source agreement boosts a
result and a strong dense-only hit can surface.
"""
from dataclasses import dataclass

from conscio.content_layer import ContentLayerManager


@dataclass
class _FTS:
    content: str = ""
    source_category: str = "system"
    content_type: str = "prose"
    rank: float = 1.0


@dataclass
class _RAG:
    content: str = ""
    score: float = 0.0


class _Store:
    def __init__(self, results):
        self._r = results

    def search(self, query, limit=10, category=None):
        return self._r[:limit]


class _World:
    def add_entity(self, **k):
        pass


class _Rag:
    def __init__(self, results):
        self._r = results

    def available(self):
        return True

    def search(self, query, limit=10):
        return self._r[:limit]


def _mgr(lexical, semantic=None):
    return ContentLayerManager(
        content_store=_Store([_FTS(content=c) for c in lexical]),
        world_model=_World(),
        session_rag_provider=(lambda: _Rag([_RAG(content=c) for c in semantic]))
        if semantic is not None else None,
    )


def test_lexical_order_preserved_without_rag():
    mgr = _mgr(["alpha", "bravo", "charlie"])
    assert mgr.recall("q", k=3) == ["alpha", "bravo", "charlie"]


def test_cross_source_agreement_ranks_first():
    # charlie is weak lexically (rank 3) but the TOP dense hit -> fused to #1.
    mgr = _mgr(lexical=["alpha", "bravo", "charlie"],
               semantic=["charlie", "delta"])
    out = mgr.recall("q", k=3)
    assert out[0] == "charlie"


def test_strong_dense_hit_enters_a_full_lexical_page():
    # lexical already fills k=3; a top dense-only hit must still surface.
    mgr = _mgr(lexical=["alpha", "bravo", "charlie"], semantic=["xray"])
    out = mgr.recall("q", k=3)
    assert "xray" in out
    assert len(out) == 3


def test_dense_disabled_is_pure_lexical():
    mgr = _mgr(["alpha", "bravo"], semantic=None)
    assert mgr.recall("q", k=2) == ["alpha", "bravo"]
