"""TDD for CompactionCheckpoint + CheckpointChain (v3.1 Ato 2)."""
import json

from conscio.checkpoint import CompactionCheckpoint, CheckpointChain


class TestCompactionCheckpoint:
    def test_creation_with_four_artifacts(self):
        cp = CompactionCheckpoint(
            durable_memory="decision: use cache",
            execution_summary="state: stable\ntools: 3\nerrors: none",
            user_requirements="build feature X",
            skill_references=["skill_a", "skill_b"],
        )
        assert cp.durable_memory == "decision: use cache"
        assert cp.execution_summary == "state: stable\ntools: 3\nerrors: none"
        assert cp.user_requirements == "build feature X"
        assert cp.skill_references == ["skill_a", "skill_b"]

    def test_byte_hash_stable(self):
        cp1 = CompactionCheckpoint(
            durable_memory="abc", execution_summary="def",
            user_requirements="ghi", skill_references=[],
        )
        cp2 = CompactionCheckpoint(
            durable_memory="abc", execution_summary="def",
            user_requirements="ghi", skill_references=[],
        )
        assert cp1.byte_hash == cp2.byte_hash

    def test_byte_hash_differs_on_change(self):
        cp1 = CompactionCheckpoint(
            durable_memory="abc", execution_summary="def",
            user_requirements="ghi", skill_references=[],
        )
        cp2 = CompactionCheckpoint(
            durable_memory="abc", execution_summary="CHANGED",
            user_requirements="ghi", skill_references=[],
        )
        assert cp1.byte_hash != cp2.byte_hash

    def test_no_rewrite_method(self):
        """Checkpoint is immutable — no update/rewrite method exists."""
        cp = CompactionCheckpoint(
            durable_memory="x", execution_summary="y",
            user_requirements="z", skill_references=[],
        )
        assert not hasattr(cp, "update")
        assert not hasattr(cp, "rewrite")
        assert not hasattr(cp, "modify")


class TestCheckpointChain:
    def test_append_first_checkpoint(self, tmp_path):
        chain = CheckpointChain(db_path=tmp_path / "checkpoints.db")
        cp = CompactionCheckpoint(
            durable_memory="first", execution_summary="v1",
            user_requirements="req", skill_references=[],
        )
        cid = chain.append(cp)
        assert cid > 0

    def test_chain_links_parent(self, tmp_path):
        chain = CheckpointChain(db_path=tmp_path / "checkpoints.db")
        cp1 = CompactionCheckpoint(
            durable_memory="first", execution_summary="v1",
            user_requirements="req", skill_references=[],
        )
        cid1 = chain.append(cp1)

        cp2 = CompactionCheckpoint(
            durable_memory="second", execution_summary="v2",
            user_requirements="req", skill_references=[],
        )
        cid2 = chain.append(cp2)

        latest = chain.latest()
        assert latest is not None
        assert latest["checkpoint_id"] == cid2
        assert latest["parent_id"] == cid1

    def test_latest_returns_none_on_empty(self, tmp_path):
        chain = CheckpointChain(db_path=tmp_path / "checkpoints.db")
        assert chain.latest() is None

    def test_get_by_id(self, tmp_path):
        chain = CheckpointChain(db_path=tmp_path / "checkpoints.db")
        cp = CompactionCheckpoint(
            durable_memory="data", execution_summary="sum",
            user_requirements="req", skill_references=["s1"],
        )
        cid = chain.append(cp)
        retrieved = chain.get(cid)
        assert retrieved is not None
        assert retrieved["durable_memory"] == "data"
        assert json.loads(retrieved["skill_references"]) == ["s1"]

    def test_chain_length(self, tmp_path):
        chain = CheckpointChain(db_path=tmp_path / "checkpoints.db")
        for i in range(5):
            cp = CompactionCheckpoint(
                durable_memory=f"mem_{i}", execution_summary=f"sum_{i}",
                user_requirements="req", skill_references=[],
            )
            chain.append(cp)
        assert chain.length() == 5

    def test_consolidate_old_checkpoints(self, tmp_path):
        """Chain compacts: after N checkpoints, old ones merge into one."""
        chain = CheckpointChain(db_path=tmp_path / "checkpoints.db", consolidate_every=3)
        for i in range(6):
            cp = CompactionCheckpoint(
                durable_memory=f"mem_{i}", execution_summary=f"sum_{i}",
                user_requirements="req", skill_references=[],
            )
            chain.append(cp)
        # After 6 appends with consolidate_every=3, should have ~4 entries
        # (3 + 3 → 1 consolidated + 3 new)
        assert chain.length() <= 5
