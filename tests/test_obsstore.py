"""ObsStore — full-fidelity, content-addressed observation storage."""
import sqlite3

import pytest

from conscio import obsstore


@pytest.fixture()
def conn(tmp_path):
    c = obsstore.connect(tmp_path / "obs.db")
    yield c
    c.close()


def test_blob_round_trips_a_large_payload_byte_for_byte(conn):
    raw = ("line %d\n" % 0).encode() * 20000  # ~140 KB, far past the old cap
    h, n, clipped = obsstore.put_blob(conn, raw)
    assert clipped is False
    assert n == len(raw)
    assert obsstore.get_blob(conn, h) == raw


def test_identical_payloads_share_one_blob(conn):
    raw = b"the same bytes exactly"
    h1, _, _ = obsstore.put_blob(conn, raw)
    h2, _, _ = obsstore.put_blob(conn, raw)
    assert h1 == h2
    assert conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 1


def test_oversized_payload_is_clipped_and_flagged(conn):
    raw = b"x" * (obsstore.MAX_FIELD_BYTES + 5000)
    h, n, clipped = obsstore.put_blob(conn, raw)
    assert clipped is True
    assert n == obsstore.MAX_FIELD_BYTES
    assert len(obsstore.get_blob(conn, h)) == obsstore.MAX_FIELD_BYTES


def test_schema_version_is_stamped(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == obsstore.SCHEMA_VERSION
