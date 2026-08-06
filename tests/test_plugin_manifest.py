"""The plugin's three manifests must agree with the package they ship."""
import json
import re
from pathlib import Path

import conscio

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "conscio" / "integrations" / "claude_code" / "assets"
PLUGIN = ASSETS / ".claude-plugin" / "plugin.json"
MCP = ASSETS / ".mcp.json"
HOOKS = ASSETS / "hooks" / "hooks.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"


def test_plugin_version_matches_package():
    assert json.loads(PLUGIN.read_text())["version"] == conscio.__version__


def test_mcp_pins_the_exact_package_version():
    args = json.loads(MCP.read_text())["mcpServers"]["conscio"]["args"]
    pin = next(a for a in args if a.startswith("conscio=="))
    assert pin == f"conscio=={conscio.__version__}"


def test_mcp_pin_is_never_a_floor():
    text = MCP.read_text()
    assert ">=" not in text, "a floor pin makes an old server indistinguishable from a bug"


def test_mcp_requests_balanced_mode():
    args = json.loads(MCP.read_text())["mcpServers"]["conscio"]["args"]
    assert args[args.index("--mode") + 1] == "balanced"


def test_hooks_cover_every_deepminer_event():
    text = HOOKS.read_text()
    for event in ("SessionStart", "PostToolUse", "PostToolUseFailure",
                  "PreCompact", "PostCompact"):
        assert event in text, event


def test_hook_commands_use_plugin_placeholders():
    text = HOOKS.read_text()
    assert "${CLAUDE_PLUGIN_ROOT}" in text
    assert "${CLAUDE_PLUGIN_DATA}" in text


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
