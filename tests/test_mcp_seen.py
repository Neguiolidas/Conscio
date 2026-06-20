# tests/test_mcp_seen.py
from conscio.mcp.seen import SeenStore


def test_first_seen_none_then_returns_stored_result(tmp_path):
    s = SeenStore(tmp_path / "mcp_seen.db")
    try:
        assert s.seen("e1") is None
        s.mark("e1", '{"event_id":"e1","noted":true}', 1750000000.0)
        assert s.seen("e1") == '{"event_id":"e1","noted":true}'
    finally:
        s.close()


def test_mark_twice_keeps_one_row(tmp_path):
    s = SeenStore(tmp_path / "mcp_seen.db")
    try:
        s.mark("e1", "{}", 1.0)
        s.mark("e1", "{}", 2.0)
        assert s.conn.execute("SELECT COUNT(*) FROM mcp_seen").fetchone()[0] == 1
    finally:
        s.close()


def test_prune_by_max_rows_keeps_newest(tmp_path):
    s = SeenStore(tmp_path / "mcp_seen.db")
    try:
        for i in range(5):
            s.mark(f"e{i}", "{}", float(i))
        s.prune(max_rows=2, max_age_days=0)
        kept = {r[0] for r in s.conn.execute("SELECT event_id FROM mcp_seen")}
        assert kept == {"e3", "e4"}
    finally:
        s.close()


def test_prune_by_age_drops_old(tmp_path):
    s = SeenStore(tmp_path / "mcp_seen.db")
    try:
        s.mark("old", "{}", 0.0)
        s.mark("new", "{}", 1_000_000.0)
        s.prune(max_rows=0, max_age_days=1, now=1_000_000.0)
        kept = {r[0] for r in s.conn.execute("SELECT event_id FROM mcp_seen")}
        assert kept == {"new"}
    finally:
        s.close()


def test_user_version_set(tmp_path):
    s = SeenStore(tmp_path / "mcp_seen.db")
    try:
        assert s.conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        s.close()
