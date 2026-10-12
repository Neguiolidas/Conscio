"""W5: upgrading from a materialized install (v4.9.2).

Slice A: the resolver adopts the legacy claude-code space (B7) and
`conscio space migrate --keep plugin|durable` resolves B3 by RENAMING the
copy not kept (never deleting). Slice B: `legacy_claude.find_leftovers`
reports the old materialized copies and `conscio init --host claude-code
--remove-legacy` moves them into a timestamped backup (identity-guarded).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ME = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"

HOOK_FILES = ["conscio_awareness.py", "conscio_deepminer.py",
              "conscio_deepminer.json", "conscio_obsstore.py",
              "conscio_wake.py", "conscio_honesty.py"]


def _v491_home(tmp_path: Path, monkeypatch) -> dict:
    """The REAL v4.9.1 materialized layout inside a tmp HOME (env-wired)."""
    home = tmp_path / "home"
    claude = home / ".claude"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CONSCIO_BASE", str(home / ".conscio"))
    monkeypatch.setenv("CLAUDE_DIR", str(claude))
    monkeypatch.setenv("CLAUDE_JSON", str(home / ".claude.json"))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("ZCODE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)

    for rel in HOOK_FILES:
        dest = claude / "hooks" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# vendored {rel}\n", encoding="utf-8")
    pkg = claude / "hooks" / "conscio_honesty_pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("# pkg\n", encoding="utf-8")
    pycache = claude / "hooks" / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "conscio_wake.cpython-310.pyc").write_bytes(b"\x00pyc")
    for rel in ("commands/conscio", "skills/conscio", "skills/conscio-relay"):
        (claude / rel).mkdir(parents=True, exist_ok=True)
        (claude / rel / "cmd.md").write_text("# cmd\n", encoding="utf-8")

    settings = {"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command":
                 f"python3 '{claude / 'hooks' / 'conscio_wake.py'}' stop"}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command":
                          f"python3 '{claude / 'hooks' / 'conscio_awareness.py'}'"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command":
                              "my-own-hook --keep-me"}]}],
    }}
    (home / ".claude" / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8")
    return {"home": home, "claude": claude, "settings": settings}


def _claude_json_entry(home: Path, storage: Path) -> None:
    """The .claude.json entry exactly the way v4.9.1 wrote it."""
    from conscio.installer import hostcfg
    path = home / ".claude.json"
    data = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    entry = hostcfg.mcp_server_entry(storage.name, flags={"relay": True},
                                     model=None)
    data.setdefault("mcpServers", {})["conscio"] = entry
    data.setdefault("other-server", {"command": "keep-me"})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert entry["args"][0] == "--storage"
    return Path(entry["args"][1])


def _legacy_space(home: Path, slug: str = "claude-code",
                  instance_id: str = ME) -> Path:
    space = home / ".conscio" / "instances" / slug
    space.mkdir(parents=True, exist_ok=True)
    (space / "instance.json").write_text(
        json.dumps({"instance_id": instance_id, "label": slug}),
        encoding="utf-8")
    return space


def _plugin_dir(tmp_path: Path | None, monkeypatch, *,
                instance_id: str | None = OTHER) -> Path:
    base = tmp_path if tmp_path is not None else Path(
        os.environ.get("HOME", "."))
    plugin = base / "plugin-data"
    plugin.mkdir(parents=True, exist_ok=True)
    if instance_id is not None:
        space = plugin / "space"
        space.mkdir(parents=True, exist_ok=True)
        (space / "instance.json").write_text(
            json.dumps({"instance_id": instance_id}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin))
    return plugin


def _space_pointer(plugin_dir: Path, target: Path) -> None:
    (plugin_dir / "space-pointer.json").write_text(
        json.dumps({"schema": 1, "target": str(target)}), encoding="utf-8")


def _resolve(storage: Path):
    from conscio.installer.durable import resolve_space
    return resolve_space(storage, env=dict(os.environ))


@pytest.fixture
def w5_home(tmp_path, monkeypatch):
    layout = _v491_home(tmp_path, monkeypatch)
    yield layout


# ── Slice A1: the resolver adopts the legacy claude-code space (B7) ─────────

def test_b7_adopts_custom_label_space(tmp_path, monkeypatch):
    """GAP 1: a legacy space under a CUSTOM label used to be silently
    orphaned (fresh mint); the resolver now adopts it (B7)."""
    home = _v491_home(tmp_path, monkeypatch)["home"]
    legacy = _legacy_space(home, slug="my-laptop", instance_id=ME)
    _claude_json_entry(home, legacy)
    monkeypatch.setenv("CLAUDECODE", "1")
    _plugin_dir(tmp_path, monkeypatch, instance_id=None)   # plugin empty

    res = _resolve(tmp_path / "plugin-data" / "space")

    assert res.kind == "B7"
    assert res.target == legacy
    assert res.repair_pointer is True
    assert str(legacy) in res.announcement
    assert res.runtime == "claude-code"


def test_b7_ignored_when_s_is_durable_target(tmp_path, monkeypatch):
    """Default-label legacy: S == durable target stays B2 (unchanged path)."""
    home = _v491_home(tmp_path, monkeypatch)["home"]
    legacy = _legacy_space(home, slug="claude-code", instance_id=ME)
    _claude_json_entry(home, legacy)
    monkeypatch.setenv("CLAUDECODE", "1")
    _plugin_dir(tmp_path, monkeypatch, instance_id=None)

    res = _resolve(tmp_path / "plugin-data" / "space")

    assert res.kind == "B2"
    assert res.target == legacy


def test_b7_ignored_without_instance_json(tmp_path, monkeypatch):
    home = _v491_home(tmp_path, monkeypatch)["home"]
    orphan = home / ".conscio" / "instances" / "my-laptop"
    orphan.mkdir(parents=True, exist_ok=True)              # no instance.json
    _claude_json_entry(home, orphan)
    monkeypatch.setenv("CLAUDECODE", "1")
    _plugin_dir(tmp_path, monkeypatch, instance_id=None)

    res = _resolve(tmp_path / "plugin-data" / "space")

    assert res.kind == "B6", "no evidence: fall through to fresh mint"


def test_b7_ignored_with_malformed_claude_json(tmp_path, monkeypatch):
    home = _v491_home(tmp_path, monkeypatch)["home"]
    _legacy_space(home, slug="my-laptop", instance_id=ME)
    (home / ".claude.json").write_text("{ corrupted", encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    _plugin_dir(tmp_path, monkeypatch, instance_id=None)

    res = _resolve(tmp_path / "plugin-data" / "space")

    assert res.kind == "B6", "malformed JSON = no evidence: fall through"


def test_b7_ignored_when_runtime_is_not_claude_code(tmp_path, monkeypatch):
    home = _v491_home(tmp_path, monkeypatch)["home"]
    _legacy_space(home, slug="my-laptop", instance_id=ME)
    _claude_json_entry(home, home / ".conscio" / "instances" / "claude-code")
    monkeypatch.setenv("ZCODE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)

    res = _resolve(tmp_path / "plugin-data" / "space")

    assert res.kind == "B6"
    assert res.runtime != "claude-code"


def test_b7_pointer_written_survives_evidence_cleanup(tmp_path, monkeypatch):
    """A1: the MCP server writes the pointer on repair_pointer, so the
    adoption survives the cleanup of the evidence (B1 next boot)."""
    home = _v491_home(tmp_path, monkeypatch)["home"]
    legacy = _legacy_space(home, slug="my-laptop", instance_id=ME)
    _claude_json_entry(home, legacy)
    plugin = _plugin_dir(tmp_path, monkeypatch, instance_id=None)

    res = _resolve(tmp_path / "plugin-data" / "space")
    assert res.kind == "B7"
    # the MCP server writes the pointer on repair_pointer:
    _space_pointer(plugin, res.target)
    # the evidence (the old MCP entry) is cleaned up:
    (home / ".claude.json").unlink()

    res2 = _resolve(tmp_path / "plugin-data" / "space")
    assert res2.kind == "B1"
    assert res2.target == legacy


# ── Slice A2: `conscio space migrate --keep plugin|durable` resolves B3 ─────

def _b3_fixture(tmp_path, monkeypatch):
    layout = _v491_home(tmp_path, monkeypatch)
    plugin = _plugin_dir(tmp_path, monkeypatch, instance_id="plugin-id-aaaa")
    durable = tmp_path / "home" / ".conscio" / "instances" / "claude-code"
    durable.mkdir(parents=True, exist_ok=True)
    (durable / "instance.json").write_text(
        json.dumps({"instance_id": "durable-id-bbbb"}), encoding="utf-8")
    return layout, plugin, durable


def _migrate(tmp_path, keep: str | None = None, proc_root=None,
             quiet_minutes: int = 0):
    """In-process: this test file already imports the copy's modules."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from conscio.cli import main as conscio_main
    if proc_root is None:
        proc_root = tmp_path / "no-proc"
    argv = ["space", "migrate", "--proc-root", str(proc_root),
            "--quiet-minutes", str(quiet_minutes)]
    if keep is not None:
        argv += ["--keep", keep]
    out, err = io.StringIO(), io.StringIO()
    old_in = sys.stdin
    sys.stdin = io.StringIO("{}")
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = conscio_main(argv)
    finally:
        sys.stdin = old_in
    result = subprocess.CompletedProcess(argv, returncode=rc,
                                         stdout=out.getvalue(),
                                         stderr=err.getvalue())
    return result


def test_migrate_keep_durable_archives_plugin_copy(tmp_path, monkeypatch):
    """keep=durable: the plugin copy is RENAMED (never deleted) to a
    .superseded-<ts> sibling and the plugin resolves B2 afterwards."""
    _b3_fixture(tmp_path, monkeypatch)
    r = _migrate(tmp_path, keep="durable")
    assert r.returncode == 0, (r.stdout, r.stderr)
    plugin_space = tmp_path / "plugin-data" / "space"
    superseded = list(plugin_space.parent.glob("space.superseded-*"))
    assert superseded, "the plugin copy must be renamed, not deleted"
    assert not plugin_space.exists()
    durable = tmp_path / "home" / ".conscio" / "instances" / "claude-code"
    assert (durable / "instance.json").exists()
    assert "superseded" in (r.stdout + r.stderr).lower()
    assert "rename" in (r.stdout + r.stderr).lower()

    from conscio.installer.durable import resolve_space
    res = resolve_space(plugin_space, env=dict(os.environ))
    assert res.kind == "B2" and res.target == durable


def test_migrate_keep_plugin_archives_durable_copy(tmp_path, monkeypatch):
    """keep=plugin: the durable copy is renamed and the normal migrate flow
    moves the plugin copy into the durable slot."""
    _b3_fixture(tmp_path, monkeypatch)
    r = _migrate(tmp_path, keep="plugin")
    assert r.returncode == 0, (r.stdout, r.stderr)
    durable = tmp_path / "home" / ".conscio" / "instances" / "claude-code"
    superseded = list(durable.parent.glob("claude-code.superseded-*"))
    assert superseded, "the durable copy must be renamed, not deleted"
    assert (durable / "instance.json").exists()
    assert json.loads((durable / "instance.json").read_text(
        encoding="utf-8"))["instance_id"] == "plugin-id-aaaa"


def test_migrate_b3_without_keep_still_refuses(tmp_path, monkeypatch):
    _b3_fixture(tmp_path, monkeypatch)
    r = _migrate(tmp_path, keep=None)
    assert r.returncode == 2
    combined = r.stdout + r.stderr
    assert "--keep plugin" in combined and "--keep durable" in combined, combined
    plugin_space = tmp_path / "plugin-data" / "space"
    assert plugin_space.exists(), "nothing touched without --keep"
    assert not list(plugin_space.parent.glob("space.superseded-*"))


def test_migrate_keep_on_non_b3_refuses_untouched(tmp_path, monkeypatch):
    """--keep on a B6 state (no conflict): exit 2, nothing touched."""
    _v491_home(tmp_path, monkeypatch)
    plugin = _plugin_dir(tmp_path, monkeypatch, instance_id="solo-id-0001")
    r = _migrate(tmp_path, keep="durable")
    assert r.returncode == 2
    assert plugin.exists() and (plugin / "space").exists()
    assert not list(plugin.glob("space.superseded-*"))


# ── Slice B: legacy_claude.find_leftovers + --remove-legacy ─────────────────

def test_find_leftovers_on_full_v491_layout(w5_home):
    from conscio.installer import legacy_claude
    _claude_json_entry(w5_home["home"],
                       w5_home["home"] / ".conscio" / "instances" / "claude-code")
    report = legacy_claude.find_leftovers(os.environ)
    files = {str(p) for p in report["files"]}
    for rel in HOOK_FILES:
        assert str(w5_home["claude"] / "hooks" / rel) in files, rel
    dirs = {str(p) for p in report["dirs"]}
    assert str(w5_home["claude"] / "commands" / "conscio") in dirs
    assert str(w5_home["claude"] / "skills" / "conscio") in dirs
    assert str(w5_home["claude"] / "skills" / "conscio-relay") in dirs
    groups = report["settings_groups"]
    assert groups == ["Stop", "SessionStart"], groups
    mcp = report["mcp_entry"]
    assert mcp is not None and mcp["args"][0] == "--storage"
    assert Path(mcp["args"][1]) == w5_home["home"] / ".conscio" / "instances" \
        / "claude-code"


def test_find_leftovers_on_empty_claude_dir(w5_home):
    from conscio.installer import legacy_claude
    shutil.rmtree(w5_home["claude"])
    Path(w5_home["home"] / ".claude.json").unlink(missing_ok=True)
    report = legacy_claude.find_leftovers(os.environ)
    assert report["files"] == [] and report["dirs"] == []
    assert report["settings_groups"] == [] and report["mcp_entry"] is None


def test_remove_legacy_moves_everything_never_deletes(w5_home, capsys):
    """B3 of the brief: move (never delete) into legacy-backup/<UTC ts>/
    keeping the relative layout; JSONs rewritten via backup_then_write_json;
    a user's own hook group and other mcpServers survive."""
    home, claude = w5_home["home"], w5_home["claude"]
    _legacy_space(home)                                   # default label: adoptable
    _claude_json_entry(home,
                       home / ".conscio" / "instances" / "claude-code")

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0
    backups = list((home / ".conscio" / "legacy-backup").glob("*"))
    assert len(backups) == 1
    backup = backups[0]
    assert (backup / ".claude" / "hooks" / "conscio_wake.py").exists()
    assert (backup / ".claude" / "hooks" / "conscio_honesty_pkg" / "__init__.py").exists()
    assert (backup / ".claude" / "commands" / "conscio" / "cmd.md").exists()
    assert not any(claude.glob("hooks/conscio_*")), "hooks must be moved out"
    assert not (claude / "commands" / "conscio").exists()
    settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert "Stop" not in settings["hooks"], "conscio hook groups removed"
    assert "UserPromptSubmit" in settings["hooks"], "user's own group intact"
    claude_json = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "conscio" not in claude_json.get("mcpServers", {})
    assert "other-server" in claude_json, "other entries survive at top level"
    assert "moved" in capsys.readouterr().out


def test_remove_legacy_idempotent_second_run(w5_home, capsys):
    from conscio.installer import cli as installer_cli
    _legacy_space(w5_home["home"])
    assert installer_cli.main(["--host", "claude-code", "--remove-legacy"]) == 0
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0
    assert "nothing to remove" in capsys.readouterr().out.lower()


def test_remove_legacy_custom_label_without_plugin_refuses(w5_home, capsys):
    """Identity guard: a custom-label legacy space with no plugin installed
    is not adopted silently — the user is told to start Claude Code once."""
    from conscio.installer import cli as installer_cli
    legacy = _legacy_space(w5_home["home"], slug="my-laptop", instance_id=ME)
    _claude_json_entry(w5_home["home"], legacy)
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 2
    assert "start Claude Code" in capsys.readouterr().out
    assert (w5_home["home"] / ".claude" / "hooks" / "conscio_wake.py").exists()


def test_remove_legacy_plugin_other_identity(w5_home, monkeypatch, capsys):
    """The plugin resolves to a DIFFERENT live identity (its own space, no
    pointer): exit 2 unless --retire-previous-space, which proceeds and says
    the legacy space stays on disk, untouched, no longer run. (F4: the MCP
    entry is the binding evidence that the legacy space is the install's.)"""
    from conscio.installer import cli as installer_cli
    legacy = _legacy_space(w5_home["home"], slug="my-laptop", instance_id=ME)
    _claude_json_entry(w5_home["home"], legacy)
    _plugin_dir(None, monkeypatch, instance_id=OTHER)

    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 2
    assert "start Claude Code" in capsys.readouterr().out

    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy",
                             "--retire-previous-space"])
    assert rc == 0
    assert legacy.exists(), "the legacy space stays on disk, untouched"
    assert (w5_home["claude"] / "hooks" / "conscio_wake.py").exists() is False


def test_remove_legacy_proceeds_when_plugin_adopted_legacy(w5_home, monkeypatch):
    """The plugin has adopted the legacy space (B1 pointer -> S): proceed."""
    from conscio.installer import cli as installer_cli
    legacy = _legacy_space(w5_home["home"], slug="my-laptop", instance_id=ME)
    plugin = _plugin_dir(None, monkeypatch, instance_id=None)
    _space_pointer(plugin, legacy)
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0
    assert not (w5_home["claude"] / "hooks" / "conscio_wake.py").exists()



def test_wizard_lists_leftovers_and_hints_remove_legacy(w5_home, capsys):
    """B2 of the brief: after the two /plugin lines, existing leftovers are
    listed and the --remove-legacy command is printed."""
    from conscio.installer import wizard
    io = _ScriptIO()
    rc = wizard.run_with(io, host="claude-code", repair=False, model=None,
                         ts="20261011-000000")
    assert rc == 0
    out = "\n".join(io.out)
    assert "/plugin marketplace add Neguiolidas/Conscio" in out
    assert "conscio_wake.py" in out
    assert "--remove-legacy" in out


def test_wizard_without_leftovers_prints_only_plugin_lines(w5_home, capsys):
    import shutil
    shutil.rmtree(w5_home["claude"])
    Path(w5_home["home"] / ".claude.json").unlink(missing_ok=True)
    from conscio.installer import wizard
    io = _ScriptIO()
    rc = wizard.run_with(io, host="claude-code", repair=False, model=None,
                         ts="20261011-000000")
    assert rc == 0
    out = "\n".join(io.out)
    assert "--remove-legacy" not in out and "conscio_wake.py" not in out


class _ScriptIO:
    """Scripted wizard IO: no prompts (claude-code exits before any)."""

    def __init__(self):
        self.out: list[str] = []

    def ask(self, prompt, default=""):
        return default

    def confirm(self, prompt, default=False):
        return False

    def echo(self, msg):
        self.out.append(msg)


# ── B4: the awareness hook notices legacy hooks (one line, stat only) ───────

def _load_awareness():
    path = (Path(__file__).resolve().parents[1]
            / "conscio/integrations/claude_code/assets/hooks/conscio_awareness.py")
    spec = importlib.util.spec_from_file_location("_awareness_w5", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_awareness_notice_present_and_absent(w5_home, capsys, monkeypatch):
    mod = _load_awareness()
    monkeypatch.setenv("CLAUDE_DIR", str(w5_home["claude"]))
    # session-start path with legacy hooks present: exactly one notice line
    payload = json.dumps({"session_id": "s", "hook_event_name": "SessionStart",
                          "source": "startup"})
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
    mod.main()
    first = capsys.readouterr().out
    assert "legacy" in first.lower()

    shutil.rmtree(w5_home["claude"] / "hooks")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
    mod.main()
    second = capsys.readouterr().out
    assert "legacy" not in second.lower()


def test_notice_survives_missing_hooks_dir(w5_home, capsys, monkeypatch):
    """M6 shape: the notice must not raise when the claude dir has no
    hooks/ directory at all."""
    mod = _load_awareness()
    monkeypatch.setenv("CLAUDE_DIR", str(w5_home["claude"] / "no-hooks"))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
        json.dumps({"session_id": "s", "source": "startup"})))
    mod.main()          # must not raise
    assert "legacy" not in capsys.readouterr().out.lower()


def test_awareness_literal_list_contract():
    """The awareness hook keeps its own literal legacy list; a contract test
    asserts it equals legacy_claude's list (same pattern as RESERVED_TYPES)."""
    mod = _load_awareness()
    from conscio.installer import legacy_claude
    assert mod.LEGACY_HOOK_FILES == legacy_claude.HOOK_FILES


# ── W5-FIX round: F1..F6 (Claude review #1710) + addendum A1/A2/A3 ───────────

def _fake_proc(root: Path, pid: str, cmdline: str) -> None:
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(cmdline.encode() + b"\x00")


def test_f1_keep_plugin_gates_the_durable_copy(tmp_path, monkeypatch):
    """F1: --keep plugin must gate the durable copy before renaming it:
    a live process whose cmdline names that path defers the rename
    (exit 2, nothing touched)."""
    _b3_fixture(tmp_path, monkeypatch)
    durable = tmp_path / "home" / ".conscio" / "instances" / "claude-code"
    fake = tmp_path / "fake-proc"
    _fake_proc(fake, "424242",
               f"python3 -m conscio.mcp.server --storage {durable}")
    r = _migrate(tmp_path, keep="plugin", proc_root=fake)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "424242" in (r.stdout + r.stderr), "the blocking pid is named"
    assert (durable / "instance.json").exists(), "durable copy untouched"
    assert not list(durable.parent.glob("claude-code.superseded-*"))


def test_f1_keep_plugin_quiet_gate_on_durable(tmp_path, monkeypatch):
    """F1 (mtime half): a freshly written DURABLE copy defers keep=plugin
    under a quiet-minutes threshold; nothing is touched. The plugin copy is
    backdated so only the durable copy is 'recent', isolating the new gate."""
    _b3_fixture(tmp_path, monkeypatch)
    import os
    import time as _time
    old = _time.time() - 3600
    plugin_space = tmp_path / "plugin-data" / "space"
    for f in plugin_space.iterdir():
        os.utime(f, (old, old))
    r = _migrate(tmp_path, keep="plugin", proc_root=tmp_path / "no-proc",
                 quiet_minutes=60)
    assert r.returncode == 2, (r.stdout, r.stderr)
    durable = tmp_path / "home" / ".conscio" / "instances" / "claude-code"
    assert (durable / "instance.json").exists()
    assert not list(durable.parent.glob("claude-code.superseded-*"))


@pytest.mark.parametrize("blob", [
    json.dumps({"mcpServers": {"conscio": {"args": ["--storage"]}}}),
    json.dumps({"mcpServers": [{"conscio": {}}]}),
    json.dumps({"mcpServers": None}),
    json.dumps([1, 2]),
    "null",
    '"just a string"',
    json.dumps({"mcpServers": {"conscio": {"args": "--storage /x"}}}),
    json.dumps({"mcpServers": {"conscio": {"args": ["--storage", 42]}}}),
])
def test_f2_wrong_shape_claude_json_is_no_evidence(tmp_path, monkeypatch,
                                                    blob):
    """F2 (+A1): any shape that is not dict -> mcpServers dict -> conscio
    dict -> list[str] with a value after --storage is no evidence:
    resolve_space falls through to B6 and never raises."""
    home = _v491_home(tmp_path, monkeypatch)["home"]
    _legacy_space(home, slug="my-laptop", instance_id=ME)
    (home / ".claude.json").write_text(blob, encoding="utf-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    _plugin_dir(tmp_path, monkeypatch, instance_id=None)

    res = _resolve(tmp_path / "plugin-data" / "space")
    assert res.kind == "B6", f"shape {blob!r} must fall through, got {res.kind}"


def test_f2_valid_entry_still_b7(tmp_path, monkeypatch):
    """F2 control: the well-shaped entry is still evidence (B7 adopt)."""
    home = _v491_home(tmp_path, monkeypatch)["home"]
    legacy = _legacy_space(home, slug="my-laptop", instance_id=ME)
    _claude_json_entry(home, legacy)
    monkeypatch.setenv("CLAUDECODE", "1")
    _plugin_dir(tmp_path, monkeypatch, instance_id=None)

    res = _resolve(tmp_path / "plugin-data" / "space")
    assert res.kind == "B7" and res.target == legacy


def test_f3_prunes_conscio_entries_only(w5_home, capsys, monkeypatch):
    """F3 (+A2): the prune is at hook-entry granularity. User entries in the
    same event (own group AND mixed group) survive byte-equal; a group drops
    only when it ends empty; an event key drops only when it ends empty."""
    home, claude = w5_home["home"], w5_home["claude"]
    _legacy_space(home)
    _claude_json_entry(home, home / ".conscio" / "instances" / "claude-code")
    settings = {"hooks": {
        "SessionStart": [
            {"hooks": [
                {"type": "command",
                 "command": "my-own-sessionstart.sh keep-me"},
                {"type": "command", "command":
                 f"python3 '{claude / 'hooks' / 'conscio_awareness.py'}'"},
            ]},
        ],
        "Stop": [
            {"hooks": [
                {"type": "command", "command":
                 f"python3 '{claude / 'hooks' / 'conscio_wake.py'}' stop"},
                {"type": "command",
                 "command": "my-own-stop-notifier.sh keep-me"},
            ]},
        ],
        "SessionEnd": [
            {"hooks": [
                {"type": "command", "command":
                 f"python3 '{claude / 'hooks' / 'conscio_wake.py'}' end"},
            ]},
        ],
    }}
    (claude / "settings.json").write_text(json.dumps(settings),
                                          encoding="utf-8")

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0, "the prune must not fail the cleanup"
    out = json.loads(
        (claude / "settings.json").read_text(encoding="utf-8"))["hooks"]
    assert out["SessionStart"][0]["hooks"] == [
        {"type": "command", "command": "my-own-sessionstart.sh keep-me"}]
    assert out["Stop"][0]["hooks"] == [
        {"type": "command", "command": "my-own-stop-notifier.sh keep-me"}]
    assert "SessionEnd" not in out, "a conscio-only event key is dropped"


def test_f4_other_runtime_spaces_never_strand_cleanup(w5_home, monkeypatch):
    """F4: 'stranded' is only the space the legacy claude install was bound
    to (MCP --storage, else the Stop hook's --storage, else the default
    claude-code slot). Live spaces of other runtimes never block it."""
    home = w5_home["home"]
    _legacy_space(home)                       # default claude-code slot live
    for slug in ("zcode", "hermes"):
        sp = home / ".conscio" / "instances" / slug
        sp.mkdir(parents=True)
        (sp / "instance.json").write_text(
            json.dumps({"instance_id": OTHER, "label": slug}),
            encoding="utf-8")
    plugin = _plugin_dir(None, monkeypatch, instance_id=None)
    _space_pointer(plugin, home / ".conscio" / "instances" / "claude-code")

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0, "other runtimes' spaces are never stranded by this cleanup"


def test_f4_stop_hook_storage_is_the_binding_evidence(w5_home, monkeypatch):
    """F4 middle link: no MCP entry, but the v4.5.4+ Stop hook carries
    --storage <space>: that space is the one the cleanup may strand."""
    home, claude = w5_home["home"], w5_home["claude"]
    legacy = _legacy_space(home, slug="my-laptop", instance_id=ME)
    settings = json.loads(
        (claude / "settings.json").read_text(encoding="utf-8"))
    settings["hooks"]["Stop"][0]["hooks"][0]["command"] = (
        f"python3 '{claude / 'hooks' / 'conscio_wake.py'}' stop "
        f"--storage {legacy}")
    (claude / "settings.json").write_text(json.dumps(settings),
                                          encoding="utf-8")
    _plugin_dir(None, monkeypatch, instance_id=OTHER)

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 2, "the bound space is live and the plugin runs elsewhere"
    assert legacy.exists(), "the bound space is untouched"
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy",
                             "--retire-previous-space"])
    assert rc == 0
    assert legacy.exists(), "with --retire it stays on disk, untouched"


def test_f4_no_binding_evidence_falls_back_to_default_slot(w5_home, monkeypatch):
    """F4 fallback (per #1724 analysis): no MCP entry and no Stop
    --storage, so the binding evidence falls back to the default
    claude-code slot. When that slot is absent (not live) nothing is
    stranded and the cleanup proceeds — even with a custom-label space on
    disk (v4.9.1 always wrote the entry, so that combination is not a
    reachable legacy state)."""
    home = w5_home["home"]
    _legacy_space(home, slug="my-laptop", instance_id=ME)   # on disk, no evidence
    # no _claude_json_entry (no MCP evidence); the fixture's Stop hook has no
    # --storage (no Stop evidence) -> fallback to instances/claude-code
    _plugin_dir(None, monkeypatch, instance_id=OTHER)
    assert not (home / ".conscio" / "instances" / "claude-code").exists()

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0, "no evidence + default slot absent -> nothing is stranded"


def test_f4_default_slot_live_is_the_protected_fallback(w5_home, monkeypatch):
    """G1 — F4 fallback, POSITIVE: no MCP entry and no Stop --storage, but
    instances/claude-code is LIVE and the plugin resolves elsewhere (its own
    space). The fallback names that live space, so the cleanup refuses
    (rc 2) unless --retire-previous-space; with it, the space stays on
    disk, untouched, no longer run."""
    home = w5_home["home"]
    _legacy_space(home, slug="claude-code", instance_id=ME)  # default slot LIVE
    # no _claude_json_entry (no MCP evidence); the fixture's Stop hook has no
    # --storage (no Stop evidence) -> fallback to instances/claude-code, live
    _plugin_dir(None, monkeypatch, instance_id=OTHER)
    default = home / ".conscio" / "instances" / "claude-code"

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 2, (
        "the live fallback space differs from the plugin's -> refuse")
    assert default.exists(), "the claude-code space stays on disk"
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy",
                             "--retire-previous-space"])
    assert rc == 0
    assert default.exists(), "with --retire it stays on disk, untouched, " \
        "no longer run"


def test_f6_unparseable_settings_json_refuses_before_moving(w5_home, capsys):
    """G2 — F6 for settings.json: an unparseable settings.json is exit 2
    with nothing touched — hooks still in place, both JSONs byte-identical,
    the file named in the output."""
    home, claude = w5_home["home"], w5_home["claude"]
    _legacy_space(home)
    _claude_json_entry(home, home / ".conscio" / "instances" / "claude-code")
    sjson = claude / "settings.json"
    truncated = '{"hooks": {'
    sjson.write_text(truncated, encoding="utf-8")
    cj = home / ".claude.json"
    cj_before = cj.read_text(encoding="utf-8")
    settings_before = sjson.read_text(encoding="utf-8")

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 2
    assert "settings.json" in capsys.readouterr().out, "the file is named"
    assert (claude / "hooks" / "conscio_wake.py").exists(), \
        "hooks still in place"
    assert sjson.read_text(encoding="utf-8") == settings_before
    assert cj.read_text(encoding="utf-8") == cj_before


def test_f5_notice_total_when_stat_raises(w5_home, monkeypatch, capsys,
                                          tmp_path):
    """F5: on 3.10-3.13 Path.exists/is_dir propagate EACCES for an unreadable
    dir; legacy_notice catches OSError and returns None, and main() proceeds
    to the consent section (which still runs)."""
    mod = _load_awareness()
    monkeypatch.setenv("CLAUDE_DIR", str(w5_home["claude"]))

    def boom(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "exists", boom)
    monkeypatch.setattr(Path, "is_dir", boom)
    old_argv = sys.argv
    sys.argv = ["conscio_awareness.py", "--storage", str(tmp_path / "space")]
    try:
        mod.main()            # must not raise
    finally:
        sys.argv = old_argv
    assert "legacy" not in capsys.readouterr().out.lower()


def test_f6_unparseable_claude_json_refuses_before_moving(w5_home, capsys):
    """F6: settings.json and .claude.json are parsed BEFORE anything moves;
    an unparseable one is exit 2 with nothing touched, naming the file."""
    home, claude = w5_home["home"], w5_home["claude"]
    _legacy_space(home)
    cj = home / ".claude.json"
    truncated = '{"numStartups": 7, "mcpServers": {'
    cj.write_text(truncated, encoding="utf-8")
    settings_before = (claude / "settings.json").read_text(
        encoding="utf-8")

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 2
    assert "claude.json" in capsys.readouterr().out, "the file is named"
    assert (claude / "hooks" / "conscio_wake.py").exists(), \
        "hooks still in place"
    assert (claude / "settings.json").read_text(
        encoding="utf-8") == settings_before
    assert cj.read_text(encoding="utf-8") == truncated


def test_f6_claude_json_rewritten_only_with_conscio_entry(w5_home, capsys):
    """F6: a .claude.json without mcpServers.conscio is left byte-identical
    (no rewrite, no .bak side file)."""
    home = w5_home["home"]
    _legacy_space(home)
    cj = home / ".claude.json"
    before = json.dumps({"other-server": {"command": "keep-me"}})
    cj.write_text(before, encoding="utf-8")

    from conscio.installer import cli as installer_cli
    rc = installer_cli.main(["--host", "claude-code", "--remove-legacy"])
    assert rc == 0
    assert cj.read_text(encoding="utf-8") == before, "untouched"
    assert not list(cj.parent.glob(".claude.json.bak.*")), \
        "no backup for a no-op rewrite"


def test_a3_packages_load_from_the_tree():
    """A3: the package under test is the tree copy, not the live install."""
    import conscio
    tree = Path(__file__).resolve().parents[1]
    cf = str(Path(conscio.__file__).resolve())
    print("conscio.__file__:", cf)
    assert cf.startswith(str(tree / "conscio")), cf
