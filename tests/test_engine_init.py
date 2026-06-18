# tests/test_engine_init.py
"""v1.9 I-S4 / B-006 — ConsciousnessEngine survives a corrupt conscio.db at
construction (the power-loss-mid-write restart case). It must quarantine the
corrupt file, recreate fresh, and NEVER crash the host. The corrupt file is
preserved on disk for forensics (policy: quarantine + recreate + preserve).

Origin: Hermet §9 Probe A reproducer, promoted to a regression test.
"""
from pathlib import Path

from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine


def _engine(d):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=d)
    e.content_layer._session_rag = _RAG_DISABLED       # hermetic: no Ollama probe
    return e


def _corrupt_files(d):
    return list(Path(d).glob("conscio.db.corrupt-*"))


def test_try_break_garbage_db_constructs_and_recovers(tmp_path):
    db = tmp_path / "conscio.db"
    db.write_bytes(b"NOT-A-SQLITE-DB\x00garbage")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert isinstance(eng.advisory(), dict)         # fresh DB is usable
    finally:
        eng.close()
    assert _corrupt_files(tmp_path), "corrupt file must be preserved, not deleted"
    assert db.exists()                                  # a fresh db replaced it


def test_try_break_truncated_db_recovers(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()
    eng.reflect()
    eng.close()
    db = tmp_path / "conscio.db"
    db.write_bytes(db.read_bytes()[:256])               # truncate mid-page
    for sidecar in ("conscio.db-wal", "conscio.db-shm"):
        p = tmp_path / sidecar
        if p.exists():
            p.unlink()
    eng2 = _engine(tmp_path)                             # must NOT raise
    try:
        eng2.advisory()
    finally:
        eng2.close()
    assert _corrupt_files(tmp_path)


_JSON_SIDECARS = ("state_summary.json", "world_model.json", "meta_cognition.json")


def test_try_break_binary_json_sidecars_construct(tmp_path):
    # I-S4: a corrupt/binary JSON sidecar (state/world/meta) must not crash
    # construction — the JSON twin of B-006 (read_text -> UnicodeDecodeError).
    for name in _JSON_SIDECARS:
        (tmp_path / name).write_bytes(b"\xff\xfe\x00 not json \xff")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert isinstance(eng.advisory(), dict)
    finally:
        eng.close()


def test_try_break_nondict_json_sidecars_construct(tmp_path):
    # Valid JSON of the WRONG type ([]), not a dict: must fall to defaults, not
    # crash on data.get(...) (AttributeError).
    for name in _JSON_SIDECARS:
        (tmp_path / name).write_text("[]")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert isinstance(eng.advisory(), dict)
    finally:
        eng.close()


def test_try_break_binary_legacy_txt_state_constructs(tmp_path):
    # Legacy text state (pre-v0.5.1), corrupt/binary, with no json sidecar present.
    (tmp_path / "state_summary.txt").write_bytes(b"\xff\xfe legacy \x00state")
    eng = _engine(tmp_path)                              # must NOT raise
    try:
        assert isinstance(eng.advisory(), dict)
    finally:
        eng.close()


def test_try_break_two_engines_one_storage_no_false_quarantine(tmp_path):
    """I-S4 / R-03: a 2nd engine opening a storage the 1st holds open (WAL) must
    NOT crash, and must NOT let _quarantine_if_corrupt false-positive on the LIVE
    db (which would quarantine a healthy store = data loss). Regression guard for
    the B-006 quick_check probe under concurrent open."""
    e1 = _engine(tmp_path)
    try:
        e1.event_bus.emit(type="reflection", category="consciousness",
                          data={"x": 1})
        e2 = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
        try:
            assert isinstance(e2.advisory(), dict)          # constructed cleanly
            assert not _corrupt_files(tmp_path)             # live db NOT quarantined
        finally:
            e2.close()
    finally:
        e1.close()


def test_try_keep_healthy_db_not_quarantined(tmp_path):
    # A normal create + reopen must NOT quarantine a healthy DB.
    _engine(tmp_path).close()
    eng = _engine(tmp_path)
    try:
        eng.advisory()
    finally:
        eng.close()
    assert not _corrupt_files(tmp_path)
