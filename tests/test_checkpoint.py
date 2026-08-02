"""TDD for CompactionCheckpoint + CheckpointChain (v3.1 Ato 2)."""
import contextlib
import json
import time

from conscio.checkpoint import CheckpointChain, CompactionCheckpoint


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


class TestConsolidationKeepsTheChainWalkable:
    """v3.9.4: `_consolidate` deletes the merged range and re-inserts one row
    under the *oldest* id. The first surviving checkpoint still pointed at the
    id of the newest deleted row — a parent that no longer exists."""

    def _chain(self, tmp_path, n=6):
        chain = CheckpointChain(tmp_path / "cp.db", consolidate_every=3)
        for i in range(n):
            chain.append(CompactionCheckpoint(
                durable_memory=f"mem {i}", execution_summary=f"sum {i}",
                user_requirements=f"req {i}", skill_references=[],
            ))
        return chain

    def test_every_parent_id_resolves(self, tmp_path):
        chain = self._chain(tmp_path)
        ids = {row["checkpoint_id"] for row in self._rows(chain)}
        for row in self._rows(chain):
            parent = row["parent_id"]
            assert parent is None or parent in ids, (
                f"checkpoint {row['checkpoint_id']} points at deleted parent {parent}")

    def test_the_chain_walks_back_to_the_root(self, tmp_path):
        chain = self._chain(tmp_path)
        rows = {r["checkpoint_id"]: r for r in self._rows(chain)}
        seen, node = 0, chain.latest()["checkpoint_id"]
        while node is not None:
            seen += 1
            assert seen <= len(rows), "cycle in the checkpoint chain"
            node = rows[node]["parent_id"]
        assert seen == len(rows)

    @staticmethod
    def _rows(chain):
        import sqlite3
        conn = sqlite3.connect(str(chain.db_path))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM checkpoints")]
        conn.close()
        return rows


class TestConnectionsAreClosedOnFailure:
    """v3.9.4: a raise between connect() and close() used to hold the handle.

    The exception → traceback → frame cycle keeps the connection alive, so only
    the *cyclic* collector frees it — refcounting alone does not. Disabling the
    GC makes the leak deterministic instead of timing-dependent.
    """

    @staticmethod
    def _fd_count():
        import os
        return len(os.listdir("/proc/self/fd"))

    def _failing_checkpoint(self):
        cp = CompactionCheckpoint(
            durable_memory="d", execution_summary="s",
            user_requirements="r", skill_references=[],
        )
        # Unserializable payload: to_dict() raises after connect() succeeded.
        object.__setattr__(cp, "skill_references", object())
        return cp

    def test_failed_append_closes_its_connection(self, tmp_path):
        import gc

        chain = CheckpointChain(tmp_path / "leak.db")
        chain.append(CompactionCheckpoint(
            durable_memory="warm", execution_summary="up",
            user_requirements="", skill_references=[],
        ))
        bad = self._failing_checkpoint()

        gc.disable()
        try:
            before = self._fd_count()
            raised = 0
            for _ in range(30):
                try:
                    chain.append(bad)
                except Exception:  # the raise is the precondition of this test
                    raised += 1
            leaked = self._fd_count() - before
        finally:
            gc.enable()

        assert raised == 30, "the fixture stopped raising — the test proves nothing"
        assert leaked == 0, f"{leaked} descriptors held after 30 failed appends"

    def test_reads_still_work_after_failed_appends(self, tmp_path):
        chain = CheckpointChain(tmp_path / "after.db")
        chain.append(CompactionCheckpoint(
            durable_memory="kept", execution_summary="s",
            user_requirements="r", skill_references=["k"],
        ))
        for _ in range(5):
            try:
                chain.append(self._failing_checkpoint())
            except Exception:
                pass
        assert chain.length() == 1
        assert chain.latest()["durable_memory"] == "kept"
        assert json.loads(chain.latest()["skill_references"]) == ["k"]


_WORKER = '''
import sys, time
from conscio.checkpoint import CheckpointChain, CompactionCheckpoint

db, tag, count, start = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
chain = CheckpointChain(db)
while time.time() < start:      # line every worker up on the same instant
    time.sleep(0.002)
for i in range(count):
    chain.append(CompactionCheckpoint(
        durable_memory=tag + "-" + str(i), execution_summary="s",
        user_requirements="u", skill_references=[]))
'''


class TestConcurrentAppendsKeepOneChain:
    """v3.9.4: appending is a read-modify-write — find the latest row, claim it
    as parent, insert. Separate processes used to run that unserialized, so two
    could read the same latest row and both claim it: the chain forks and
    walking back from the tip silently skips history. Measured before the fix
    with this exact shape (4 processes × 25 appends): 4 of 5 runs forked, one
    of them producing three roots.
    """

    def test_four_processes_produce_one_unbroken_chain(self, tmp_path):
        import os
        import sqlite3
        import subprocess
        import sys
        from pathlib import Path

        import conscio.checkpoint as cp_mod

        db = tmp_path / "chain.db"
        CheckpointChain(db)                     # schema first; the race is over appends
        script = tmp_path / "worker.py"
        script.write_text(_WORKER)

        env = {**os.environ,
               "PYTHONPATH": str(Path(cp_mod.__file__).resolve().parents[1])}
        start = time.time() + 0.5
        procs = [
            subprocess.Popen(
                [sys.executable, str(script), str(db), f"w{i}", "25", str(start)],
                env=env, stderr=subprocess.PIPE, text=True)
            for i in range(4)
        ]
        for p in procs:
            _, err = p.communicate(timeout=120)
            assert p.returncode == 0, f"worker failed: {err.strip()[-400:]}"

        with contextlib.closing(sqlite3.connect(db)) as conn:
            rows = conn.execute(
                "SELECT checkpoint_id, parent_id FROM checkpoints"
            ).fetchall()

        ids = {cid for cid, _ in rows}
        parents = [pid for _, pid in rows if pid is not None]

        assert len(rows) == 100, "every append must land"
        assert len(parents) == len(set(parents)), \
            "two checkpoints claim the same parent — the chain forked"
        assert len(rows) - len(parents) == 1, \
            f"a chain has exactly one root, found {len(rows) - len(parents)}"
        assert set(parents) <= ids, "a parent_id points at no row"
