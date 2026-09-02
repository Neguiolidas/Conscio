# tests/test_installer_isolation.py
"""Tests for the per-agent space isolation guard (installer).

Covers:
- spaces.space_is_cross_agent: same-owner paths pass, foreign-owner paths block
- spaces.liaison_db_path: resolves to the agent's own space
- hostcfg.upsert_conscio_entry: cross-agent write raises HostConfigError
"""
import pytest

from conscio.installer import hostcfg, spaces


class TestSpaceIsCrossAgent:
    def test_same_user_home_is_not_cross(self):
        assert spaces.space_is_cross_agent(
            "/home/ubuntu/.conscio/instances/hermet",
            "/home/ubuntu",
        ) is False

    def test_subdirectory_of_same_home_is_not_cross(self):
        assert spaces.space_is_cross_agent(
            "/home/ubuntu/.gemini/antigravity/space",
            "/home/ubuntu",
        ) is False

    def test_foreign_user_home_is_cross(self):
        assert spaces.space_is_cross_agent(
            "/home/ubuntu/.conscio/instances/hermet",
            "/home/outra-pessoa",
        ) is True

    def test_different_foreign_owner(self):
        assert spaces.space_is_cross_agent(
            "/home/ubuntu/anything",
            "/home/carol",
        ) is True

    def test_empty_or_invalid_inputs_never_block(self):
        assert spaces.space_is_cross_agent("", "/home/ubuntu") is False
        assert spaces.space_is_cross_agent("/some/space", "") is False

    def test_windows_style_path_is_not_cross_for_same_owner(self):
        # different separator conventions must not false-positive on Linux
        assert spaces.space_is_cross_agent(
            "/home/ubuntu/x", "/home/ubuntu"
        ) is False


class TestLiaisonDbPath:
    def test_liaison_lives_inside_the_space(self):
        p = spaces.liaison_db_path("hermet")
        assert p.name == "liaison.db"
        assert p.parent == spaces.space_dir("hermet")

    def test_different_slugs_get_different_liaison(self):
        assert spaces.liaison_db_path("a") != spaces.liaison_db_path("b")


class TestUpsertCrossAgentGuard:
    def test_cross_owner_write_raises(self):
        cfg = {}
        with pytest.raises(hostcfg.HostConfigError):
            hostcfg.upsert_conscio_entry(
                cfg, "hermet", flags={}, model=None,
                owner_home="/home/outra-pessoa")

    def test_same_owner_write_passes(self, tmp_path, monkeypatch):
        # Isolate the space root so the entry writes to a temp dir
        monkeypatch.setattr(
            spaces, "INSTANCES_ROOT", lambda: tmp_path / "instances")
        cfg = {}
        hostcfg.upsert_conscio_entry(
            cfg, "hermet", flags={}, model=None,
            owner_home=str(tmp_path))
        entry = cfg.get("mcpServers", {}).get("conscio")
        assert entry, "entry must be written for the same-owner case"
        storage_idx = entry["args"].index("--storage")
        assert tmp_path.as_posix() in entry["args"][storage_idx + 1]

    def test_no_owner_home_passes_unchanged(self):
        # Legacy callers (no owner_home) keep old behaviour — no guard
        cfg = {}
        hostcfg.upsert_conscio_entry(cfg, "x", flags={}, model=None)
        assert "conscio" in cfg.get("mcpServers", {})
