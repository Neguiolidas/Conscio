import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from conscio.agency.contracts import AuditVerdict
from conscio.agency.trial import run_trial, untrusted_envelope
from conscio.content_store import ContentStore
from conscio.daemon import _arg_parser
from conscio.installer import extras
from conscio.squads.experts.auditor import AuditorVoice
from conscio.squads.experts.optimizer import OptimizerVoice
from conscio.squads.experts.qa import QAVoice
from conscio.squads.opositors.caustic import CausticVoice
from conscio.squads.opositors.devils_advocate import DevilsAdvocateVoice
from conscio.squads.opositors.douche_reviewer import DoucheReviewerVoice
from conscio.squads.opositors.skeptic_engineer import SkepticEngineerVoice
from conscio.structural_consent import ConsentScope, StructuralConsent, consent_path
from conscio.vector_backend import VectorBackend

# =====================================================================
# 1. Consent fixes
# =====================================================================

def test_extras_graphify_consent_syntax():
    steps = extras.REGISTRY["graphify"].enable(Path("/path/to/space"))
    cmd = steps[1]
    # Positional argument 'structure', NOT '--scope structure'
    assert "conscio consent structure --storage /path/to/space" in cmd
    assert "--scope" not in cmd


def test_structural_consent_reload_if_changed(tmp_path):
    c_path = consent_path(tmp_path)
    consent = StructuralConsent(c_path)
    assert consent.scope_for("ws-1") == ConsentScope.OFF
    assert not consent.reload_if_changed()

    # Modify file externally
    time.sleep(0.01)
    other = StructuralConsent(c_path)
    other.grant("ws-1", ConsentScope.PROJECT)

    # File was updated on disk
    reloaded = consent.reload_if_changed()
    assert reloaded is True
    assert consent.scope_for("ws-1") == ConsentScope.PROJECT

    # Second call without changes returns False
    assert not consent.reload_if_changed()


def test_yolo_help_mentions_current_workspace():
    parser = _arg_parser()
    help_str = parser.format_help()
    assert "--yolo" in help_str
    # Verified it clarifies current workspace instead of falsely claiming any workspace
    assert "current workspace" in help_str


# =====================================================================
# 2. Noosphere untrusted envelope
# =====================================================================

def test_untrusted_envelope_format():
    env = untrusted_envelope("malicious prompt injection")
    assert "[UNTRUSTED FOREIGN DATA]" in env
    assert "[/UNTRUSTED FOREIGN DATA]" in env
    assert "malicious prompt injection" in env


def test_run_trial_wraps_in_untrusted_envelope(tmp_path):
    class SpySkeptic:
        def __init__(self):
            self.audited_goals = []
            self.audited_rationales = []

        def audit(self, proposal, *, goal_text, tool_doc=""):
            self.audited_goals.append(goal_text)
            self.audited_rationales.append(proposal.rationale)
            return AuditVerdict(verdict="PASS", reasons=[])

    from conscio.agency.tools import make_default_registry
    reg = make_default_registry(sandbox_root=tmp_path)
    spy = SpySkeptic()
    steps = [{"tool": "fs_read", "args": {"path": "a.txt"}, "rationale": "read foreign file"}]
    (tmp_path / "a.txt").write_text("hello")

    out = run_trial(steps, goal_text="foreign goal", skeptic=spy, registry=reg)
    assert out.passed is True
    assert len(spy.audited_goals) == 1
    assert "[UNTRUSTED FOREIGN DATA]" in spy.audited_goals[0]
    assert "foreign goal" in spy.audited_goals[0]
    assert "[UNTRUSTED FOREIGN DATA]" in spy.audited_rationales[0]
    assert "read foreign file" in spy.audited_rationales[0]


# =====================================================================
# 3. Squads fixes
# =====================================================================

@pytest.mark.parametrize("voice_cls,keyword", [
    (AuditorVoice, "security risk found"),
    (OptimizerVoice, "performance risk slow n+1"),
    (QAVoice, "coverage gap edge case untested"),
    (CausticVoice, "bad and terrible design"),
    (DevilsAdvocateVoice, "what if assumption fails"),
    (DoucheReviewerVoice, "bad slop code smell"),
    (SkepticEngineerVoice, "why unnecessary complex"),
])
def test_all_seven_voices_recalculate_vote_in_analyze_llm(voice_cls, keyword):
    voice = voice_cls()
    ctx = {"question": "Refactor codebase", "context": "Clean clean simple"}

    # Base deterministic analysis returns proceed
    base = voice.analyze(ctx)
    assert base.vote == "proceed"

    # LLM adapter generates risk keywords
    mock_adapter = MagicMock()
    mock_adapter.generate.return_value = MagicMock(text=f"Critical {keyword}!")

    llm_result = voice.analyze_llm(ctx, adapter=mock_adapter)
    assert len(llm_result.concerns) > 0
    # Crucial: vote was recalculated from concerns, NOT left as 'proceed'
    assert llm_result.vote in ("hold", "veto")


def test_skeptic_engineer_reasonable_stack():
    voice = SkepticEngineerVoice()
    # Complex token ('microservices' or 'distributed') but combined with reasonable stack
    ctx = {
        "question": "Deploy backend",
        "context": "distributed setup using fastapi + postgres and redis — simple stack, straightforward",
    }
    res = voice.analyze(ctx)
    assert "reasonable stack detected" in res.analysis
    assert "complex stack detected" not in res.analysis
    assert res.vote == "proceed"


# =====================================================================
# 4. Knowledge Store (B1 - B6)
# =====================================================================

def test_b6_index_empty_content(tmp_path):
    store = ContentStore(db_path=tmp_path / "conscio.db")
    try:
        res = store.index_ex("test_empty", "", "reflection")
        assert res.status == "empty"
        assert res.source_id == 0
        assert res.chunks_added == 0
        assert not res.is_new_content

        # Also via index()
        sid = store.index("test_empty_2", "", "reflection")
        assert sid == 0
    finally:
        store.close()


def test_b1_tombstone_filter_trigram(tmp_path):
    store = ContentStore(db_path=tmp_path / "conscio.db")
    try:
        # Create separate conscio_trigram.db (rebuilt state)
        store.index("source_active", "supercalifragilistic unique query phrase", "reference")
        s2 = store.index("source_stale", "supercalifragilistic other phrase", "reference")
        store.rebuild_db()

        # Mark source_stale as tombstoned
        store._mark_stale(s2, "superseded")

        # Search with trigram: tombstoned source should NOT appear
        res = store.search("supercalifragilistic", use_trigram=True, include_stale=False)
        labels = [r.title for r in res]
        assert any("source_active" in l for l in labels)
        assert not any("source_stale" in l for l in labels)
    finally:
        store.close()


def test_b2_compact_delete_stats_post_rebuild(tmp_path):
    store = ContentStore(db_path=tmp_path / "conscio.db")
    try:
        s1 = store.index("source1", "content one", "reference")
        store.index("source2", "content two", "reference")
        store.rebuild_db()

        # chunks_trigram is now dropped from main db and lives in conscio_trigram.db
        assert not (tmp_path / "conscio.db").parent.joinpath("conscio_trigram.db").exists() is False

        # stats() must not throw OperationalError
        st = store.stats()
        assert st["source_count"] == 2
        assert st["trigram_chunk_count"] == 2

        # delete_source() must not throw OperationalError
        ok = store.delete_source(s1)
        assert ok is True

        # compact() must not throw OperationalError
        removed = store.compact(before_days=0)
        assert removed == 1
    finally:
        store.close()


def test_b3_chunk_count_updated_on_category_added(tmp_path):
    store = ContentStore(db_path=tmp_path / "conscio.db")
    try:
        # Index initial content
        res1 = store.index_ex("multi_cat", "same duplicate text body", "reference")
        assert res1.status == "new"
        src = store.get_source(res1.source_id)
        assert src.chunk_count == 1

        # Index same content under new category
        res2 = store.index_ex("multi_cat", "same duplicate text body", "reflection")
        assert res2.status == "category_added"
        src2 = store.get_source(res1.source_id)
        # chunk_count must be updated to 2
        assert src2.chunk_count == 2
    finally:
        store.close()


def test_b4_vector_backend_add_batch_dedup(tmp_path):
    backend = VectorBackend(db_path=tmp_path / "vectors.db", dimension=3)
    try:
        # Batch containing duplicate IDs
        items = [
            ("id1", [0.1, 0.2, 0.3]),
            ("id1", [0.4, 0.5, 0.6]),
            ("id2", [0.7, 0.8, 0.9]),
        ]
        count = backend.add_batch(items)
        # Deduped: only 2 unique IDs written
        assert count == 2
        assert backend.stats()["vectors"] == 2
        conn = backend._conn_get()
        row = conn.execute("SELECT embedding FROM vectors WHERE id='id1'").fetchone()
        assert row is not None
        vec = backend._deserialize(row[0])
        assert pytest.approx(vec[0], rel=1e-4) == 0.4
    finally:
        backend.close()


def test_b5_cache_double_copy(tmp_path):
    store = ContentStore(db_path=tmp_path / "conscio.db")
    try:
        store.index("sample", "alpha beta gamma delta", "reference")

        # First search populates cache
        res1 = store.search("alpha")
        assert len(res1) == 1

        # Mutate the returned list
        res1.clear()

        # Second search must NOT return empty list from corrupted cache
        res2 = store.search("alpha")
        assert len(res2) == 1
    finally:
        store.close()
