"""v3.8 DeepMiner — hostile suite (destructive: TRY to break the invariants).

Complements ``tests/test_deepminer.py`` (which *confirms* behaviour). To avoid
duplication this file targets only invariants that suite does **not** already
exercise — each test earns its place by pushing harder or covering a gap:

  * I7  ``compress_observations`` never writes the platform-owned handoff file;
        its ONLY persistence is ``content_store.index()`` under a deepminer label.
  * I1  a *corrupt schema* (tables dropped mid-run) still returns ``-1`` and the
        agent keeps running — not just one softly-swallowed write.
  * I10 a 10 MB output is clipped to MAX_FIELD_BYTES, no OOM/hang.
  * Non-ASCII (accents/emoji/CJK) survive a write→read roundtrip byte-for-byte.
  * ``compress_observations`` is deterministic with NO LLM adapter (0 tokens).
  * ``set_session`` default routing coexists with an explicit ``session_id=``
        override in the same engine, staying isolated (the real MCP usage).
"""

from __future__ import annotations

import glob
import time

import pytest

from conscio import obsstore
from conscio.engine import ConsciousnessEngine


@pytest.fixture
def eng(tmp_path):
    e = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s", delivery_check=False)
    yield e
    e.close()


def test_compress_never_writes_platform_handoff(eng):
    """I7: DeepMiner persists ONLY via content_store.index — never _session_handoff.md."""
    seen: list[dict] = []
    orig = eng.content_store.index

    def spy(**kw):
        seen.append(kw)
        return orig(**kw)

    setattr(eng.content_store, "index", spy)  # noqa: B010 — instance spy, not a constant

    eng.observe("edit_file", "fix auth", "done", "/p")
    r = eng.compress_observations()

    assert r["count"] == 1
    # exactly one persistence call, through the deepminer-owned label
    assert len(seen) == 1
    assert seen[0]["label"].startswith("handoff_deepminer_")
    assert seen[0]["category"] == "session"
    # nothing wrote the platform-owned handoff anywhere under storage
    assert not glob.glob(str(eng.storage / "**" / "_session_handoff.md"), recursive=True)


def test_observe_corrupt_schema_survives(eng):
    """I1: tables dropped mid-run → observe returns -1 repeatedly, agent never crashes."""
    assert eng.observe("t", "i", "o") >= 1  # opens + caches the connection
    conn = eng._obs_conn()
    conn.execute("DROP TABLE observations")
    conn.execute("DROP TABLE obs_fts")
    conn.commit()
    # every subsequent write fails softly — never raises, always -1
    assert eng.observe("t2", "i2", "o2") == -1
    assert eng.observe("t3", "i3", "o3") == -1
    # recall over the corrupt db is swallowed too → [] (no crash)
    assert eng.recall_observations("i2") == []


def test_observe_10mb_output_clipped_at_sanity_cap_no_oom(eng):
    """I10: a 10 MB output is clipped to MAX_FIELD_BYTES, no OOM/hang.

    The number moved with v3.9 (payloads are stored whole up to a sanity cap);
    the guarantee did not — a pathological payload must never hang the agent.
    """
    huge = "z" * 10_000_000
    t0 = time.time()
    oid = eng.observe("big", "in", huge, "/p")
    assert oid >= 1
    assert time.time() - t0 < 3.0  # clipping happens before the write, never after
    got = obsstore.read_observation(eng._obs_conn(), oid)
    assert len(got["output"]) == obsstore.MAX_FIELD_BYTES
    assert got["truncated"] is True


def test_unicode_emoji_roundtrip(eng):
    """Accents / emoji / CJK survive write→read exactly (UTF-8 in, UTF-8 out)."""
    oid = eng.observe("café", "héllo 🌍 dïacrítics", "日本語 ✓ output", "/pró")
    got = obsstore.read_observation(eng._obs_conn(), oid)
    assert (got["tool"], got["input"], got["output"], got["project"]) == (
        "café", "héllo 🌍 dïacrítics", "日本語 ✓ output", "/pró")


def test_compress_deterministic_without_adapter(eng):
    """compress runs with no LLM adapter (0 tokens); count is stable across calls."""
    for i in range(3):
        eng.observe("edit_file", f"change {i}", "done", "/p")
    r1 = eng.compress_observations()
    r2 = eng.compress_observations()
    assert r1["count"] == r2["count"] == 3
    assert r1["handoff"]  # non-empty handoff produced offline


def test_setsession_default_coexists_with_explicit_override(eng):
    """set_session routes the default sid; an explicit session_id= overrides it — isolated."""
    eng.set_session("primary")
    eng.observe("toolP", "p-in", "p-out")                      # default → primary
    eng.observe("toolX", "x-in", "x-out", session_id="other")  # explicit override

    rp = eng.compress_observations()          # defaults to primary
    ro = eng.compress_observations("other")

    assert rp["count"] == 1 and rp["session_id"] == "primary"
    assert ro["count"] == 1 and ro["session_id"] == "other"
    # no cross-session leakage in either direction
    assert "toolP" in rp["handoff"] and "toolX" not in rp["handoff"]
    assert "toolX" in ro["handoff"] and "toolP" not in ro["handoff"]
