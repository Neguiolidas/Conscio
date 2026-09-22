"""v4.6.7: a shipped command must not consume a variable nobody sets.

`/conscio:awake` told the agent to run
``conscio daemon --storage "$CONSCIO_SPACE" --awake``. Nothing in the repository
ever set ``CONSCIO_SPACE`` — the only occurrence was the line consuming it — so
the flag expanded empty, which falls through to the default space, whose
directory the engine then creates and in which the daemon mints an identity.

Two substitutions are legitimate in a command file because the *host* performs
them: ``$ARGUMENTS`` and ``${CLAUDE_*}``. A ``$CONSCIO_*`` reference is
different in kind: those are read from a process environment, and the shell that
runs a slash command has none of them set. Citing one is therefore always the
bug above, never a working feature.

This guard exists because the defect was found by accident, while checking an
unrelated name collision. It turns that luck into a sweep, over a directory no
Python test looked at — the same shape as `test_plugin_hook_paths.py`, which
came from the same family of failure: a manifest naming something that does not
travel with it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

COMMANDS = (Path(__file__).resolve().parents[1]
            / "conscio/integrations/claude_code/assets/commands")

_CONSCIO_VAR = re.compile(r"\$\{?(CONSCIO_[A-Z0-9_]+)\}?")


def _command_files() -> list[Path]:
    return sorted(COMMANDS.glob("*.md"))


def test_the_command_directory_is_actually_there():
    """Positive control: an empty sweep must not read as a clean sweep."""
    files = _command_files()
    assert files, f"no command files under {COMMANDS} — the guard proves nothing"
    assert any("$ARGUMENTS" in f.read_text(encoding="utf-8") for f in files), \
        "no command uses $ARGUMENTS — the regex is probably not seeing the text"


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.name)
def test_command_cites_no_unset_conscio_variable(path: Path):
    found = sorted(set(_CONSCIO_VAR.findall(path.read_text(encoding="utf-8"))))
    assert not found, (
        f"{path.name} consumes {found}, which nothing sets for a slash command. "
        f"It will expand empty. Resolve the value in the package instead — that "
        f"is what conscio.space.resolve_live_space is for.")
