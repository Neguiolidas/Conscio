# tests/test_semantic_dedup.py
from conscio.output_filter import SemanticDedup, STAGE_REGISTRY, build_stage
from conscio.semantic import SemanticEngine


class StubEmbedder:
    _VOCAB = {
        "operational": [1, 0, 0, 0], "online": [1, 0, 0, 0],
        "owns": [0, 0, 1, 0],
    }
    def embed(self, text):
        return [float(x) for x in self._VOCAB.get((text or "").strip().lower(), [0, 1, 0, 0])]
    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _stub_engine():
    return SemanticEngine(embedder=StubEmbedder(), axes=[])


def test_annotates_near_dup_keeps_both_blocks():
    stage = SemanticDedup(semantic=_stub_engine(), threshold=0.88)
    text = "operational\n\noperational\n\nowns"
    out = stage.apply(text)
    blocks = out.split("\n\n")
    assert blocks[0] == "operational"            # first block untouched
    assert "near-dup" in blocks[1]               # second annotated
    assert blocks[1].startswith("operational")   # but kept verbatim
    assert blocks[2] == "owns"                    # unrelated block untouched


def test_below_threshold_untouched():
    stage = SemanticDedup(semantic=_stub_engine(), threshold=0.88)
    out = stage.apply("operational\n\nowns")
    assert "near-dup" not in out


def test_offline_no_op_with_dead_engine():
    class Dead:
        def available(self):
            return False
        def cosine(self, a, b):
            return 1.0
    stage = SemanticDedup(semantic=Dead())
    text = "operational\n\noperational"
    assert stage.apply(text) == text


def test_offline_no_op_when_semantic_none():
    stage = SemanticDedup(semantic=None)
    text = "operational\n\noperational"
    assert stage.apply(text) == text


def test_registered_in_stage_registry():
    assert STAGE_REGISTRY.get("semantic_dedup") is SemanticDedup
    assert SemanticDedup(semantic=None).name() == "semantic_dedup"


def test_build_stage_constructs_semantic_dedup():
    stage = build_stage("semantic_dedup", {"threshold": 0.9})
    assert isinstance(stage, SemanticDedup)
    assert stage.threshold == 0.9


class RealisticEmbedder:
    """Like a real embedder: empty/whitespace text → [] (no vector)."""
    def embed(self, text):
        t = (text or "").strip().lower()
        if not t:
            return []
        return {"operational": [1.0, 0, 0, 0]}.get(t, [0.0, 1.0, 0, 0])
    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def test_empty_blocks_not_annotated():
    # Consecutive blank lines create empty blocks. With a realistic embedder
    # (empty → []), cosine is 0.0 → empty blocks are never annotated as near-dups
    # (StubEmbedder masks this by giving "" a non-empty vector).
    stage = SemanticDedup(
        semantic=SemanticEngine(embedder=RealisticEmbedder(), axes=[]), threshold=0.88)
    out = stage.apply("operational\n\n\n\noperational")
    assert "near-dup" not in out


def test_single_block_no_op():
    stage = SemanticDedup(semantic=_stub_engine(), threshold=0.88)
    assert stage.apply("operational") == "operational"


def test_marker_renders_score():
    stage = SemanticDedup(semantic=_stub_engine(), threshold=0.88,
                          marker=" [dup {score:.2f}]")
    out = stage.apply("operational\n\noperational")
    assert "[dup 1.00]" in out
