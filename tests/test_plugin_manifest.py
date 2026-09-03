"""The plugin's three manifests must agree with the package they ship."""
import json
import re
import shlex
import sqlite3
import subprocess
from pathlib import Path

import conscio

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "conscio" / "integrations" / "claude_code" / "assets"
PLUGIN = ASSETS / ".claude-plugin" / "plugin.json"
MCP = ASSETS / ".mcp.json"
HOOKS = ASSETS / "hooks" / "hooks.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"


def _hook_commands():
    """Every command string the plugin registers, flattened."""
    return [hook["command"]
            for entries in json.loads(HOOKS.read_text())["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]]


def test_plugin_version_matches_package():
    assert json.loads(PLUGIN.read_text())["version"] == conscio.__version__


def test_mcp_pins_the_exact_package_version():
    args = json.loads(MCP.read_text())["mcpServers"]["conscio"]["args"]
    pin = next(a for a in args if a.startswith("conscio=="))
    assert pin == f"conscio=={conscio.__version__}"


def test_mcp_pin_is_never_a_floor():
    text = MCP.read_text()
    assert ">=" not in text, "a floor pin makes an old server indistinguishable from a bug"


def test_mcp_does_not_pin_a_mode():
    """The template must not hard-code --mode: a fresh install defaults to
    balanced via resolve_mode, and an EXISTING install's persisted mode must
    survive a rebuild of the manifest. Hard-coding --mode balanced here would
    downgrade an ultra user on every plugin update (the v4.5.1/4.5.2 regression)."""
    args = json.loads(MCP.read_text())["mcpServers"]["conscio"]["args"]
    assert "--mode" not in args, "template must not pin --mode"


def test_hooks_cover_every_deepminer_event():
    text = HOOKS.read_text()
    for event in ("SessionStart", "PostToolUse", "PostToolUseFailure",
                  "PreCompact", "PostCompact"):
        assert event in text, event


def test_hook_commands_use_plugin_placeholders():
    text = HOOKS.read_text()
    assert "${CLAUDE_PLUGIN_ROOT}" in text
    assert "${CLAUDE_PLUGIN_DATA}" in text


def test_obsstore_argument_points_at_the_vendored_module():
    """--obsstore names the module to import, not the database to write.

    v4.0.0 shipped it pointing at ``${CLAUDE_PLUGIN_DATA}/obs.db``. The hook
    fails open, so every event exited 0 and recorded nothing.
    """
    for command in _hook_commands():
        if "--obsstore" not in command:
            continue
        argv = shlex.split(command)
        value = argv[argv.index("--obsstore") + 1]
        assert value.endswith(".py"), command


def test_the_shipped_manifest_actually_records(tmp_path):
    """Run PostToolUse exactly as Claude Code would and look for the row.

    The placeholder and path-shape checks above are string assertions: they
    stay green for any manifest that merely mentions the right words. Only
    executing the shipped command proves capture is wired.
    """
    command = next(c for c in _hook_commands() if "post-tool-use " in c)
    argv = shlex.split(command.replace("${CLAUDE_PLUGIN_ROOT}", str(ASSETS))
                              .replace("${CLAUDE_PLUGIN_DATA}", str(tmp_path)))
    payload = {"tool_name": "Bash", "tool_input": {"command": "true"},
               "tool_response": {"stdout": "ok"}, "session_id": "s1",
               "cwd": str(tmp_path)}
    subprocess.run(argv, input=json.dumps(payload), text=True,
                   capture_output=True, timeout=60, check=True)

    db = tmp_path / "space" / "obs.db"
    assert db.exists(), "the hook recorded nothing and still exited 0"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_marketplace_points_at_the_asset_dir():
    entry = json.loads(MARKETPLACE.read_text())["plugins"][0]
    assert entry["name"] == "conscio"
    assert (ROOT / entry["source"]).resolve() == ASSETS.resolve()


def test_every_manifest_is_valid_json():
    for path in (PLUGIN, MCP, HOOKS, MARKETPLACE):
        json.loads(path.read_text())


def test_readme_does_not_promise_an_unreleased_version():
    readme = (ASSETS / "README.md").read_text()
    for found in re.findall(r"conscio==([0-9.]+)", readme):
        assert found == conscio.__version__, found
