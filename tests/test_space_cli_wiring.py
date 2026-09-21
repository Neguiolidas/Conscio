"""v4.6.7: the entrypoints are WIRED to the resolver, not merely near it.

v4.6.6 nearly shipped a correct function that nothing called: the test proved
the function and the bug lived in the `main` that never used it. So this file
exercises the call sites, and then sweeps for the ones that got away.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pytest

from conscio.liaison import directory

_ID = "dddddddd-4444-4444-8444-dddddddddddd"
_PKG = Path(__file__).resolve().parents[1] / "conscio"

# The only legitimate callers left. `mailbox.resolve_db` consults the legacy
# global db to migrate out of it; the MCP server's constructor keeps a fallback
# for the case its `main` did not resolve one. Everything a person types must go
# through conscio.space instead.
_ALLOWED = {
    "default_db": {"liaison/mailbox.py", "mcp/server.py"},
    "default_storage": {"noosphere/paths.py", "space.py"},
}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A machine where exactly one agent published exactly one real space."""
    monkeypatch.setenv("CONSCIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    monkeypatch.delenv("CONSCIO_SPACE", raising=False)
    monkeypatch.delenv("CONSCIO_SELF_ID", raising=False)

    space = tmp_path / "live-space"
    space.mkdir()
    (space / "instance.json").write_text(
        json.dumps({"instance_id": _ID, "label": "t", "created_ts": 0.0}),
        encoding="utf-8")
    directory.publish_self(_ID, space=str(space))
    return space


def test_cli_storage_funnel_follows_the_card(live):
    """`_storage` feeds every `conscio` subcommand that takes a space."""
    from conscio.cli import _storage
    assert _storage("") == str(live)
    assert _storage("/explicit") == "/explicit", "an explicit path must still win"


def test_relay_db_resolution_follows_the_card(live):
    """`quarantine` read the empty global db while the real mailbox had 182."""
    from conscio.liaison.relay_cli import _db_for
    args = argparse.Namespace(storage="", liaison_db="")
    assert _db_for(args) == live / "liaison.db"


def test_noosphere_cli_follows_the_card(live):
    """The `id` path MINTS an identity — resolving it wrong forges an agent."""
    from conscio.noosphere.cli import _live
    assert _live("") == live


def _direct_calls(path: Path, name: str) -> bool:
    """True if this module calls `name()` itself (not just imports it)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Call)
               and ((isinstance(n.func, ast.Name) and n.func.id == name)
                    or (isinstance(n.func, ast.Attribute) and n.func.attr == name))
               for n in ast.walk(tree))


@pytest.mark.parametrize("name", sorted(_ALLOWED))
def test_no_new_caller_sneaks_back_onto_the_dead_default(name):
    """C4: one resolver. A second one is how the two answers drift apart."""
    offenders = sorted(
        str(p.relative_to(_PKG)) for p in _PKG.rglob("*.py")
        if "assets" not in p.parts and _direct_calls(p, name))
    assert set(offenders) <= _ALLOWED[name], (
        f"{sorted(set(offenders) - _ALLOWED[name])} call {name}() directly; "
        f"route them through conscio.space.resolve_live_space instead")


def test_no_cli_hands_a_raw_arg_to_a_library_that_resolves_it_itself():
    """The gap a direct sweep for default_storage() cannot see.

    `publish`, `importer`, `record_publish` and `audit` all take
    `storage=None -> default`, which is correct for a library. The CLI was
    handing them `args.storage` — empty when the operator passed no flag — so
    the resolution happened one layer below the point where intent was known,
    and all four reach `load_or_create`, which WRITES. Four minting paths that
    looked like read paths.

    The resolution has to happen where the operator is, and the result passed
    down.

    `cli.py` is allowed: it hands `args.storage` to `_cmd_observatory`, which
    resolves through `resolve_live_space` itself. Passing the raw argument is
    only wrong when whatever receives it would resolve to the neutral default —
    that is the distinction this allowlist records, since the text of the call
    looks identical either way.
    """
    allowed = {"cli.py"}
    offenders = {str(p.relative_to(_PKG)) for p in _PKG.rglob("*.py")
                 if "assets" not in p.parts
                 and "storage=args.storage" in p.read_text(encoding="utf-8")}
    assert offenders <= allowed, (
        f"{sorted(offenders - allowed)} pass the raw CLI argument to a function "
        f"that resolves it to the default; resolve first and pass the result")


def test_the_sweep_can_actually_see_a_caller():
    """Positive control: an empty result must not read as a clean result."""
    assert _direct_calls(_PKG / "space.py", "default_storage")
