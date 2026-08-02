"""TDD: Hallways — wing/room/drawer hierarchy."""
import pytest

from conscio.hallways import Hallways


def test_hallways_create_wing(tmp_path):
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("projects")
    wings = hw.list_wings()
    assert "projects" in wings
    assert "default" in wings  # auto-created
    hw.close()


def test_hallways_create_room(tmp_path):
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("projects")
    hw.create_room("projects", "pentest")
    rooms = hw.list_rooms("projects")
    assert "pentest" in rooms
    hw.close()


def test_hallways_assign_drawer(tmp_path):
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("projects")
    hw.create_room("projects", "pentest")
    hw.assign_drawer("projects", "pentest", drawer_id=42)
    drawers = hw.list_drawers("projects", "pentest")
    assert 42 in drawers
    hw.close()


def test_hallways_default(tmp_path):
    # Sem wing/room → default/default
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.assign_drawer(drawer_id=1)
    drawers = hw.list_drawers("default", "default")
    assert 1 in drawers
    hw.close()


def test_hallways_persistence(tmp_path):
    db = tmp_path / "hw.db"
    hw = Hallways(db_path=db)
    hw.create_wing("conscio")
    hw.close()
    hw2 = Hallways(db_path=db)
    assert "conscio" in hw2.list_wings()
    hw2.close()


def test_hallways_list_drawers_by_wing(tmp_path):
    """list_drawers(wing) sem room → drawers de todas rooms."""
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("projects")
    hw.create_room("projects", "pentest")
    hw.create_room("projects", "security")
    hw.assign_drawer("projects", "pentest", drawer_id=1)
    hw.assign_drawer("projects", "security", drawer_id=2)
    drawers = hw.list_drawers(wing="projects")
    assert 1 in drawers and 2 in drawers
    hw.close()


def test_hallways_remove_drawer(tmp_path):
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("x")
    hw.create_room("x", "y")
    hw.assign_drawer("x", "y", drawer_id=99)
    hw.remove_drawer(99)
    drawers = hw.list_drawers("x", "y")
    assert 99 not in drawers
    hw.close()


def test_hallways_stats(tmp_path):
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("a")
    hw.create_room("a", "r1")
    hw.create_room("a", "r2")
    hw.assign_drawer("a", "r1", drawer_id=1)
    s = hw.stats()
    assert s["wings"] >= 2  # 'default' + 'a'
    assert s["rooms"] >= 3
    assert s["drawers"] == 1
    hw.close()


def test_hallways_dump(tmp_path):
    """Backup via sqlite3 backup API."""
    hw = Hallways(db_path=tmp_path / "hw.db")
    hw.create_wing("test")
    target = tmp_path / "backup.db"
    hw.dump(target)
    assert target.exists()
    assert target.stat().st_size > 0
    hw.close()


def test_dump_closes_the_destination_when_the_copy_fails(tmp_path, monkeypatch):
    """v3.9.4: `dump()` was the one method that touched the shared connection
    without `self._lock`, and it also left the destination handle open on any
    error. The lock is unprovable from the outside — SQLite is built
    SERIALIZED, so the shared handle is already mutex-protected and the lock
    buys snapshot consistency, not survival. The leak is provable."""
    import sqlite3

    hw = Hallways(db_path=tmp_path / "hw.db")
    closed: list[str] = []
    real_connect = sqlite3.connect

    class _Tracked:
        def __init__(self, conn):
            self._conn = conn

        def close(self):
            closed.append("dst")
            self._conn.close()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: _Tracked(real_connect(*a, **k)))

    def _explode():
        raise RuntimeError("backup source unavailable")

    monkeypatch.setattr(hw, "_conn_get", _explode)

    with pytest.raises(RuntimeError):
        hw.dump(tmp_path / "copy.db")

    assert closed == ["dst"]
    monkeypatch.undo()
    hw.close()
