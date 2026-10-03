"""The plugin's shipped version fields must never drift apart (v4.8.1).

The zcode CLI decides whether a plugin has an update by comparing the
INSTALLED version against the marketplace entry's own ``version`` field
(``glm/zcode.cjs``: ``comparePluginVersions`` returns ``"none"`` as soon
as either side is missing). A marketplace entry without ``version``
therefore hides every new release from the auto-update flow.

These tests pin every shipped copy of the version to the single source
(``conscio.__version__``) so a release cannot bump one and forget the
others.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from conscio import __version__

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _REPO_ROOT / "conscio" / "integrations" / "claude_code" / "assets"
_MARKETPLACE = _REPO_ROOT / ".claude-plugin" / "marketplace.json"


def _marketplace_entry() -> dict:
    manifest = json.loads(_MARKETPLACE.read_text(encoding="utf-8"))
    entries = [p for p in manifest["plugins"] if p.get("name") == "conscio"]
    assert len(entries) == 1, (
        f"expected exactly one 'conscio' entry in {_MARKETPLACE.name}, "
        f"found {len(entries)}"
    )
    return entries[0]


def test_marketplace_entry_has_a_version():
    entry = _marketplace_entry()
    version = entry.get("version")
    assert isinstance(version, str) and version.strip(), (
        "the marketplace entry must carry an explicit 'version': without "
        "it the zcode auto-update compares against nothing and never "
        "offers the new release"
    )


def test_marketplace_version_matches_plugin_json():
    entry = _marketplace_entry()
    plugin_json = json.loads(
        (_ASSETS / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert entry["version"] == plugin_json["version"], (
        "marketplace.json and plugin.json versions diverged: the "
        "auto-update compares the installed version against the "
        "MARKETPLACE entry, so a drift ships wrong update signals"
    )


def test_marketplace_version_matches_package_version():
    entry = _marketplace_entry()
    assert entry["version"] == __version__, (
        f"marketplace.json says {entry['version']!r} but the package is "
        f"{__version__!r}; bump them together (docs/RELEASING.md step 1)"
    )


def test_plugin_json_matches_package_version():
    plugin_json = json.loads(
        (_ASSETS / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert plugin_json["version"] == __version__


def test_shipped_mcp_json_pins_the_same_version():
    mcp_json = json.loads((_ASSETS / ".mcp.json").read_text(encoding="utf-8"))
    args = mcp_json["mcpServers"]["conscio"]["args"]
    pins = [a for a in args if isinstance(a, str) and a.startswith("conscio==")]
    assert len(pins) == 1, f"expected exactly one conscio== pin in .mcp.json args, got {pins}"
    pinned = pins[0].split("==", 1)[1]
    assert pinned == __version__, (
        f".mcp.json uvx pin is {pinned!r}, package is {__version__!r}: "
        "a stale pin makes the plugin's MCP server run an old conscio "
        "even after the plugin cache updates"
    )


def test_marketplace_version_is_semver():
    entry = _marketplace_entry()
    assert re.fullmatch(r"\d+\.\d+\.\d+", entry["version"]), (
        "the auto-update comparison coerces both sides through semver; a "
        "non-semver marketplace version degrades the update signal to "
        "'version-changed'"
    )
