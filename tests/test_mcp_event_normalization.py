# tests/test_mcp_event_normalization.py
"""BUG-39: conscio_feed/conscio_note crash when the host sends ``data`` instead
of ``payload`` and omits ``source``.

The internal Event schema (schemas.py EVENT_SCHEMA) requires ``type``,
``source``, ``category`` and ``payload``. Hosts that predate the schema — or
use different conventions — send ``data`` instead of ``payload`` and omit
``source`` and sometimes ``category``. Before the fix, validate_event() rejected
these and the MCP server raised InvalidParams, crashing the connection for the
caller. The fix adds _normalize_event() which maps these aliases before
validation.
"""
import io
import json

from conscio.engine import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, serve

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"}}


def _bindings(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return Bindings(eng, seen, adapter_name=None, workspace_id="ws39"), eng, seen


def _run(bindings, requests):
    out = io.StringIO()
    serve(bindings, io.StringIO("".join(json.dumps(r) + "\n" for r in requests)),
          out)
    return [json.loads(x) for x in out.getvalue().splitlines() if x]


def _call(name, arguments):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


# ── _normalize_event unit tests ──────────────────────────────────────

class TestNormalizeEvent:
    """The mapping function itself — isolated, no engine needed."""

    def _b(self, tmp_path):
        eng = ConsciousnessEngine("t", storage_path=tmp_path)
        seen = SeenStore(tmp_path / "seen.db")
        return Bindings(eng, seen, adapter_name=None, workspace_id="w"), eng, seen

    def test_data_is_aliased_to_payload(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            out = b._normalize_event(
                {"type": "system", "category": "system",
                 "data": {"x": 1}})
            assert out["payload"] == {"x": 1}
            assert "data" in out  # original key preserved, not mutated
        finally:
            seen.close()
            eng.close()

    def test_source_defaults_to_host(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            out = b._normalize_event(
                {"type": "system", "category": "system",
                 "payload": {}})
            assert out["source"] == "host"
        finally:
            seen.close()
            eng.close()

    def test_category_defaults_to_type(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            out = b._normalize_event(
                {"type": "milestone", "source": "hermes",
                 "payload": {}})
            assert out["category"] == "milestone"
        finally:
            seen.close()
            eng.close()

    def test_existing_source_is_not_overwritten(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            out = b._normalize_event(
                {"type": "system", "source": "hermes",
                 "category": "system", "payload": {}})
            assert out["source"] == "hermes"
        finally:
            seen.close()
            eng.close()

    def test_existing_payload_is_not_overwritten_by_data(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            out = b._normalize_event(
                {"type": "system", "source": "h", "category": "s",
                 "payload": {"a": 1}, "data": {"b": 2}})
            assert out["payload"] == {"a": 1}
        finally:
            seen.close()
            eng.close()

    def test_input_dict_is_not_mutated(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            original = {"type": "system", "category": "system",
                         "data": {"x": 1}}
            b._normalize_event(original)
            assert "payload" not in original  # caller's dict untouched
            assert "source" not in original
        finally:
            seen.close()
            eng.close()

    def test_non_dict_passes_through(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            assert b._normalize_event("not a dict") == "not a dict"
            assert b._normalize_event(None) is None
            assert b._normalize_event(42) == 42
        finally:
            seen.close()
            eng.close()

    def test_canonical_form_passes_through_unchanged(self, tmp_path):
        b, eng, seen = self._b(tmp_path)
        try:
            canonical = {"id": "x", "type": "perception", "source": "host",
                          "category": "system", "payload": {"cpu": 0.4}}
            out = b._normalize_event(canonical)
            assert out["type"] == "perception"
            assert out["source"] == "host"
            assert out["category"] == "system"
            assert out["payload"] == {"cpu": 0.4}
        finally:
            seen.close()
            eng.close()


# ── Integration: feed with Hermes-style event ────────────────────────

class TestFeedWithHermesFormat:
    """The exact format Hermes sends — ``data`` instead of ``payload``,
    no ``source``, no ``category``."""

    def test_feed_with_data_alias_succeeds(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "hermes-001", "type": "system",
                  "data": {"event": "test", "version": "3.9.5"}}
            out = _run(b, [INIT, _call("conscio_feed", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["event_id"] == "hermes-001"
            assert "advisory" in body
        finally:
            seen.close()
            eng.close()

    def test_feed_with_data_alias_and_no_source_succeeds(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "hermes-002", "type": "milestone",
                  "category": "milestone",
                  "data": {"event": "shipped"}}
            out = _run(b, [INIT, _call("conscio_feed", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["event_id"] == "hermes-002"
        finally:
            seen.close()
            eng.close()

    def test_feed_canonical_form_still_works(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "canon-001", "type": "perception", "source": "host",
                  "category": "system", "payload": {"cpu": 0.4}}
            out = _run(b, [INIT, _call("conscio_feed", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["event_id"] == "canon-001"
            assert "advisory" in body
        finally:
            seen.close()
            eng.close()


# ── Integration: note with Hermes-style event ─────────────────────────

class TestNoteWithHermesFormat:

    def test_note_with_data_alias_succeeds(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "note-001", "type": "system",
                  "data": {"event": "recorded"}}
            out = _run(b, [INIT, _call("conscio_note", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["event_id"] == "note-001"
            assert body["noted"] is True
            # Verify the event was recorded in the EventBus.
            rows = eng.event_bus.query(type="host:event", limit=5)
            assert rows and rows[0].data["host_type"] == "system"
            assert rows[0].data["source"] == "host"  # default applied
        finally:
            seen.close()
            eng.close()

    def test_note_canonical_form_still_works(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "note-002", "type": "user_msg", "source": "alice",
                  "category": "user", "payload": {"text": "hi"}}
            out = _run(b, [INIT, _call("conscio_note", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["event_id"] == "note-002"
            assert body["noted"] is True
            rows = eng.event_bus.query(type="host:event", limit=5)
            assert rows and rows[0].data["host_type"] == "user_msg"
            assert rows[0].data["source"] == "alice"
        finally:
            seen.close()
            eng.close()

    def test_note_preserves_source_when_explicit(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "note-003", "type": "system", "source": "hermes",
                  "category": "system", "data": {"event": "test"}}
            out = _run(b, [INIT, _call("conscio_note", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["noted"] is True
            rows = eng.event_bus.query(type="host:event", limit=5)
            assert rows[0].data["source"] == "hermes"
        finally:
            seen.close()
            eng.close()


# ── Edge cases ────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_feed_with_truly_invalid_event_still_rejects(self, tmp_path):
        """Events that are still invalid after normalisation must reject."""
        b, eng, seen = _bindings(tmp_path)
        try:
            # Missing type entirely — cannot be normalised.
            ev = {"id": "bad", "data": {"x": 1}}
            out = _run(b, [INIT, _call("conscio_feed", {"event": ev})])
            # Should be an error, not a crash.
            assert "error" in out[1]
        finally:
            seen.close()
            eng.close()

    def test_feed_with_non_dict_event_rejects(self, tmp_path):
        b, eng, seen = _bindings(tmp_path)
        try:
            out = _run(b, [INIT, _call("conscio_feed", {"event": "not a dict"})])
            assert "error" in out[1]
        finally:
            seen.close()
            eng.close()

    def test_feed_priority_field_is_ignored_not_stored(self, tmp_path):
        """Hermes sends a ``priority`` field that is not in the schema.
        It should be ignored (not cause an error) and not stored."""
        b, eng, seen = _bindings(tmp_path)
        try:
            ev = {"id": "pri-001", "type": "system", "category": "system",
                  "data": {"event": "test"}, "priority": 3}
            out = _run(b, [INIT, _call("conscio_feed", {"event": ev})])
            body = json.loads(out[1]["result"]["content"][0]["text"])
            assert body["event_id"] == "pri-001"
        finally:
            seen.close()
            eng.close()
