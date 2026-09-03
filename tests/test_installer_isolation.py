# tests/test_installer_isolation.py
"""Tests for the per-agent space isolation guard (installer).

Isolation is based on the space's OWN identity (instance.json): a space that
already has a different owner than the caller is cross-agent → write blocked,
read still allowed. Not home-dir heuristics (agents cohabit one home).
"""
import pytest

from conscio.installer import hostcfg, spaces
from conscio.noosphere.identity import load_or_create, NoosphereIdentityError


def _make_space(base, slug):
    """Create a real space (identity) under ``base`` for the given slug."""
    d = base / "instances" / slug
    identity = load_or_create(d)
    return d, identity


class TestSpaceIsCrossAgent:
    def test_new_space_no_identity_is_not_cross(self, tmp_path):
        # space does not exist yet / no one owns it → first claimer decides
        space = str(tmp_path / "instances" / "hermet")
        assert spaces.space_is_cross_agent(space, "self-abc") is False

    def test_empty_dir_is_not_cross(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir(parents=True)
        assert spaces.space_is_cross_agent(str(d), "self-abc") is False

    def test_cross_agent_blocks(self, tmp_path):
        space, owner = _make_space(tmp_path, "gemini")
        assert spaces.space_is_cross_agent(str(space), "self-hermet") is True

    def test_same_owner_passes(self, tmp_path):
        space, owner = _make_space(tmp_path, "hermet")
        assert spaces.space_is_cross_agent(str(space), owner.instance_id) is False

    def test_blank_args_never_block(self):
        assert spaces.space_is_cross_agent("", "x") is False
        assert spaces.space_is_cross_agent("/some/space", "") is False

    def test_corrupt_identity_fails_closed(self, tmp_path):
        d = tmp_path / "instances" / "broken"
        d.mkdir(parents=True)
        (d / "instance.json").write_text("{corrupt-not-json")
        assert spaces.space_is_cross_agent(str(d), "self-abc") is True


class TestLiaisonDbPath:
    def test_liaison_lives_inside_the_space(self):
        p = spaces.liaison_db_path("hermet")
        assert p.name == "liaison.db"
        assert p.parent == spaces.space_dir("hermet")

    def test_different_slugs_get_different_liaison(self):
        assert spaces.liaison_db_path("a") != spaces.liaison_db_path("b")


class TestUpsertCrossAgentGuard:
    def test_cross_agent_write_raises(self, tmp_path, monkeypatch):
        d, owner = _make_space(tmp_path, "gemini")
        monkeypatch.setattr(spaces, "INSTANCES_ROOT", lambda: tmp_path / "instances")
        cfg = {}
        with pytest.raises(hostcfg.HostConfigError):
            hostcfg.upsert_conscio_entry(
                cfg, "gemini", flags={}, model=None,
                self_instance_id="self-hermet-wrong-id")

    def test_same_agent_write_passes(self, tmp_path, monkeypatch):
        d, owner = _make_space(tmp_path, "hermet")
        monkeypatch.setattr(spaces, "INSTANCES_ROOT", lambda: tmp_path / "instances")
        cfg = {}
        hostcfg.upsert_conscio_entry(
            cfg, "hermet", flags={}, model=None,
            self_instance_id=owner.instance_id)
        assert "conscio" in cfg.get("mcpServers", {})

    def test_new_slug_any_self_writes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(spaces, "INSTANCES_ROOT", lambda: tmp_path / "instances")
        cfg = {}
        hostcfg.upsert_conscio_entry(
            cfg, "brand-new", flags={}, model=None,
            self_instance_id="self-anything")
        assert "conscio" in cfg.get("mcpServers", {})

    def test_no_self_passes_unchanged(self):
        # Legacy callers (no self) keep old behaviour — no guard
        cfg = {}
        hostcfg.upsert_conscio_entry(cfg, "x", flags={}, model=None)
        assert "conscio" in cfg.get("mcpServers", {})