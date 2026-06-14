# tests/test_agency_tools.py
"""Tests for ToolRegistry, risk levels, sandbox guard and built-in tools."""
import pytest

from conscio.agency.contracts import ToolResult
from conscio.agency.tools import (
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    Risk,
    ToolRegistry,
    make_default_registry,
)


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    return root


class _FakeStore:
    def __init__(self):
        self.notes = []

    def index(self, label, content, category, **kw):
        self.notes.append((label, content, category))
        return 1


class _FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)
        return 1


class TestRegistry:
    def test_register_and_dispatch(self):
        reg = ToolRegistry()
        reg.register("echo", lambda text: text.upper(),
                     params={"text": {"type": "str", "required": True}},
                     risk=Risk.LOW, description="uppercase echo")
        result = reg.dispatch("echo", {"text": "hi"})
        assert isinstance(result, ToolResult)
        assert result.ok and result.output == "HI"

    def test_duplicate_name_raises(self):
        reg = ToolRegistry()
        reg.register("x", lambda: "", params={}, risk=Risk.LOW, description="")
        with pytest.raises(ValueError):
            reg.register("x", lambda: "", params={}, risk=Risk.LOW,
                         description="")

    def test_unknown_tool_returns_failed_result(self):
        result = ToolRegistry().dispatch("ghost", {})
        assert not result.ok and "unknown tool" in result.error

    def test_tool_exception_is_captured(self):
        reg = ToolRegistry()
        reg.register("boom", lambda: 1 / 0, params={}, risk=Risk.LOW,
                     description="")
        result = reg.dispatch("boom", {})
        assert not result.ok and "ZeroDivisionError" in result.error

    def test_catalog_text_lists_tools_with_risk(self):
        reg = ToolRegistry()
        reg.register("a", lambda: "", params={}, risk=Risk.HIGH,
                     description="dangerous thing")
        text = reg.catalog_text()
        assert "a" in text and "high" in text and "dangerous thing" in text


class TestSandboxedFs:
    def test_fs_write_then_read_roundtrip(self, sandbox):
        reg = make_default_registry(sandbox_root=sandbox)
        w = reg.dispatch("fs_write", {"path": "notes.md", "content": "hello"})
        assert w.ok
        r = reg.dispatch("fs_read", {"path": "notes.md"})
        assert r.ok and r.output == "hello"

    def test_path_traversal_is_blocked(self, sandbox):
        reg = make_default_registry(sandbox_root=sandbox)
        for evil in ("../escape.txt", "/etc/passwd", "a/../../escape"):
            result = reg.dispatch("fs_read", {"path": evil})
            assert not result.ok, evil
            assert "sandbox" in result.error

    def test_write_size_cap(self, sandbox):
        reg = make_default_registry(sandbox_root=sandbox)
        big = "x" * (MAX_WRITE_BYTES + 1)
        result = reg.dispatch("fs_write", {"path": "big.txt", "content": big})
        assert not result.ok and "size" in result.error

    def test_fs_read_missing_file_fails_cleanly(self, sandbox):
        reg = make_default_registry(sandbox_root=sandbox)
        assert not reg.dispatch("fs_read", {"path": "nope.md"}).ok


class TestMemoryAndEvents:
    def test_memory_note_indexes_into_store(self, sandbox):
        store = _FakeStore()
        reg = make_default_registry(sandbox_root=sandbox, content_store=store)
        result = reg.dispatch("memory_note", {"text": "remember this"})
        assert result.ok and store.notes
        label, content, category = store.notes[0]
        assert content == "remember this" and category == "external"

    def test_emit_event_uses_external_type(self, sandbox):
        bus = _FakeBus()
        reg = make_default_registry(sandbox_root=sandbox, event_bus=bus)
        result = reg.dispatch("emit_event", {"text": "something happened"})
        assert result.ok
        assert bus.events[0]["type"] == "tool_call"
        assert bus.events[0]["category"] == "external"
        assert bus.events[0]["data"]["text"] == "something happened"

    def test_builtins_absent_without_backends(self, sandbox):
        reg = make_default_registry(sandbox_root=sandbox)
        assert "memory_note" not in reg.names()
        assert "emit_event" not in reg.names()
        assert {"fs_read", "fs_write"} <= set(reg.names())


# ── F2: precheck + goal_update ──────────────────────────────────────────

def test_fs_precheck_blocks_traversal_without_executing(tmp_path):
    reg = make_default_registry(sandbox_root=tmp_path / "sb")
    spec = reg.get("fs_write")
    assert spec.precheck is not None
    err = spec.precheck({"path": "../outside.txt", "content": "x"})
    assert err is not None and "sandbox" in err
    assert spec.precheck({"path": "inside.txt", "content": "x"}) is None


def test_fs_read_precheck_absolute_path(tmp_path):
    reg = make_default_registry(sandbox_root=tmp_path / "sb")
    err = reg.get("fs_read").precheck({"path": "/etc/passwd"})
    assert err is not None


def test_goal_update_complete_and_cancel(tmp_path):
    from conscio.goal_generator import GoalGenerator, GoalPriority
    gg = GoalGenerator(tmp_path, [])
    goal = gg.add_user_goal("tidy the sandbox", GoalPriority.HIGH)
    reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                goal_generator=gg)
    result = reg.dispatch("goal_update",
                          {"action": "complete", "goal_id": goal.id})
    assert result.ok
    assert all(g.id != goal.id for g in gg.active_goals())


def test_goal_update_unknown_goal_fails_cleanly(tmp_path):
    from conscio.goal_generator import GoalGenerator
    gg = GoalGenerator(tmp_path, [])
    reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                goal_generator=gg)
    result = reg.dispatch("goal_update",
                          {"action": "cancel", "goal_id": "nope"})
    assert not result.ok


def test_goal_update_absent_without_generator(tmp_path):
    reg = make_default_registry(sandbox_root=tmp_path / "sb")
    assert reg.get("goal_update") is None


# ── v1.2: fs_read size cap (MAX_READ_BYTES) ─────────────────────────────

def test_fs_read_rejects_oversized_file(tmp_path):
    reg = make_default_registry(sandbox_root=tmp_path)
    (tmp_path / "big.txt").write_text("x" * (MAX_READ_BYTES + 1))
    result = reg.dispatch("fs_read", {"path": "big.txt"})
    assert not result.ok and "size cap" in result.error


def test_fs_read_allows_small_file(tmp_path):
    reg = make_default_registry(sandbox_root=tmp_path)
    (tmp_path / "ok.txt").write_text("hello")
    result = reg.dispatch("fs_read", {"path": "ok.txt"})
    assert result.ok and result.output == "hello"
