"""Tests for W4a: Claude Code plugin is the only install path.

1. `conscio init --host claude-code` (normal and --repair) writes NOTHING
   (no space, no ~/.claude file, no settings.json, no ~/.claude.json),
   prints the two /plugin lines, and exits 0.
2. `govern status` finds the capture space via `known_plugin_data_dirs`,
   and reports 'no capture space' when none exists.
"""
from __future__ import annotations

from pathlib import Path


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    res: dict[str, bytes] = {}
    if not root.exists():
        return res
    for p in sorted(root.rglob("*")):
        if p.is_file():
            res[str(p.relative_to(root))] = p.read_bytes()
    return res


def test_wizard_claude_code_writes_nothing_normal_and_repair(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    (home / "existing.txt").write_text("keep me", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_JSON", raising=False)
    monkeypatch.delenv("CONSCIO_BASE", raising=False)

    before = _snapshot_tree(home)

    from conscio.installer import cli as icli

    # Normal run
    rc = icli.main(["--host", "claude-code"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "/plugin marketplace add Neguiolidas/Conscio" in out
    assert "/plugin install conscio" in out
    after_normal = _snapshot_tree(home)
    assert after_normal == before, f"Files were created/modified: {set(after_normal) - set(before)}"

    # Repair run
    rc_repair = icli.main(["--host", "claude-code", "--repair"])
    assert rc_repair == 0
    out_repair = capsys.readouterr().out
    assert "/plugin marketplace add Neguiolidas/Conscio" in out_repair
    assert "/plugin install conscio" in out_repair
    after_repair = _snapshot_tree(home)
    assert after_repair == before, f"Files were created/modified: {set(after_repair) - set(before)}"


def test_govern_status_finds_plugin_data_dir(tmp_path, monkeypatch, capsys):
    from conscio import cli

    # (a) When none exists: reports 'no capture space'
    home = tmp_path / "home_empty"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("ZCODE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CONSCIO_BASE", raising=False)

    rc = cli._cmd_govern("status", None, "", False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no capture space" in out

    # (b) When plugin data dir exists: finds it via known_plugin_data_dirs
    plugin_dir = tmp_path / "custom_plugin_data"
    space_dir = plugin_dir / "space"
    space_dir.mkdir(parents=True)
    (space_dir / "obs.db").write_bytes(b"\0" * (2 * 1_048_576))  # 2.0 MB
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))

    rc = cli._cmd_govern("status", None, "", False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2.0 MB" in out
    assert "no capture space" not in out
