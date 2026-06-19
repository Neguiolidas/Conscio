# tests/test_v2_debt_zero.py
"""v2.0 debt-zero carry-over from the v1.9 ledger.

R-09: world_model / meta_cognition / context_manager wrote JSON via plain
      write_text → a power-loss / tailing reader mid-write sees a truncated
      file. Now atomic (guards.atomic_write_text: tmp + os.replace).
R-02: quarantined conscio.db.corrupt-<ts> copies accumulated forever. Now the
      newest few are kept, the rest pruned.
(R-05 dedup-metadata stays DEFERRED → scheduled into the v2.0.1 Full-act plan.)
"""
import json
import os

import pytest

from conscio.guards import atomic_write_text


# ── R-09: atomic writes ───────────────────────────────────────────────────────
def test_atomic_write_text_writes_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_text(p, '{"a": 1}')
    assert p.read_text() == '{"a": 1}'
    assert not (tmp_path / "x.json.tmp").exists()


def test_atomic_write_text_preserves_original_when_replace_fails(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    p.write_text('{"old": true}')

    def boom(src, dst):
        raise OSError("simulated power loss before rename completes")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, '{"new": true}')
    assert json.loads(p.read_text()) == {"old": True}    # original intact


def test_world_model_save_is_atomic_no_tmp(tmp_path):
    from conscio.world_model import WorldModel
    wm = WorldModel(tmp_path)
    wm.add_entity("a", "system", state="ok")             # triggers _save
    assert (tmp_path / "world_model.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_meta_cognition_save_is_atomic_no_tmp(tmp_path):
    from conscio.meta_cognition import MetaCognition
    mc = MetaCognition(tmp_path)
    mc.record_error("timeout")                           # triggers _save
    assert (tmp_path / "meta_cognition.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


# ── R-02: corrupt-db quarantine pruning ───────────────────────────────────────
def test_prune_quarantine_keeps_newest_and_drops_sidecars(tmp_path):
    from conscio.engine import _prune_quarantine
    db = tmp_path / "conscio.db"
    for stamp in ("20200101000000000000", "20200102000000000000",
                  "20200103000000000000"):
        (tmp_path / f"conscio.db.corrupt-{stamp}").write_text("x")
    (tmp_path / "conscio.db.corrupt-20200101000000000000-wal").write_text("w")
    _prune_quarantine(db, keep=2)
    names = sorted(p.name for p in tmp_path.glob("conscio.db.corrupt-*"))
    assert names == ["conscio.db.corrupt-20200102000000000000",
                     "conscio.db.corrupt-20200103000000000000"]


def test_corrupt_db_quarantine_pruned_on_construct(tmp_path):
    from conscio.engine import _quarantine_if_corrupt
    db = tmp_path / "conscio.db"
    for stamp in ("20200101000000000000", "20200102000000000000",
                  "20200103000000000000", "20200104000000000000"):
        (tmp_path / f"conscio.db.corrupt-{stamp}").write_text("old garbage")
    db.write_bytes(b"\xff\xfe not a sqlite database \x00")
    dest = _quarantine_if_corrupt(db)                    # quarantines + prunes
    assert dest is not None and dest.exists()
    assert len(list(tmp_path.glob("conscio.db.corrupt-*"))) <= 3
