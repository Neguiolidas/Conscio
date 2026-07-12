"""Metabolic tiers are driven by the host's real context usage (Lote 2, a).

The metabolic tier system (ACTIVE/FATIGUE/CRITICAL) was dead in the default
wiring: nothing set ``engine.session_tokens_used``, so ``reflect()`` fell back
to the tiny injection-string size (~0.1% of any window) and the tier was always
"healthy". Conscio cannot know the host's live context usage on its own, so the
host reports it: ``conscio.feed`` accepts an optional ``session_tokens`` and
wires it to the engine before reflecting.
"""
from conscio.engine import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


def _bindings(tmp_path, model="glm-5.1"):     # glm-5.1 -> 200k window
    eng = ConsciousnessEngine(model, storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return Bindings(eng, seen, adapter_name=None, workspace_id="ws"), eng, seen


def _event(eid):
    return {"id": eid, "type": "perception", "source": "host",
            "category": "system", "payload": {"world_state": "ok"}}


def test_feed_session_tokens_sets_engine_usage(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        b._tools()["conscio.feed"]({"event": _event("e1"),
                                    "session_tokens": 150_000})
        assert eng.session_tokens_used == 150_000
    finally:
        seen.close(); eng.close()


def test_feed_session_tokens_drives_critical_tier(tmp_path):
    b, eng, seen = _bindings(tmp_path)         # 150k / 200k = 75% -> CRITICAL
    try:
        b._tools()["conscio.feed"]({"event": _event("e2"),
                                    "session_tokens": 150_000})
        assert "critical" in eng._state.metabolic.lower()
    finally:
        seen.close(); eng.close()


def test_feed_without_session_tokens_leaves_usage_none(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        b._tools()["conscio.feed"]({"event": _event("e3")})
        assert eng.session_tokens_used is None
    finally:
        seen.close(); eng.close()


def test_feed_ignores_bad_session_tokens(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        b._tools()["conscio.feed"]({"event": _event("e4"),
                                    "session_tokens": -5})
        assert eng.session_tokens_used is None
    finally:
        seen.close(); eng.close()
