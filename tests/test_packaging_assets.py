"""The plugin's dotfiles must survive the build. Globs do not match them by default."""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "conscio/integrations/claude_code/assets"

REQUIRED = [
    f"{PREFIX}/.claude-plugin/plugin.json",
    f"{PREFIX}/.mcp.json",
    f"{PREFIX}/hooks/hooks.json",
    f"{PREFIX}/hooks/conscio_awareness.py",
    f"{PREFIX}/hooks/conscio_deepminer.py",
    f"{PREFIX}/hooks/conscio_obsstore.py",
]


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
                   cwd=ROOT, check=True, capture_output=True)
    wheel = next(out.glob("*.whl"))
    return zipfile.ZipFile(wheel).namelist()


def test_required_assets_are_in_the_wheel(wheel_names):
    missing = [p for p in REQUIRED if p not in wheel_names]
    assert not missing, f"missing from wheel: {missing}"


def test_all_fourteen_commands_are_in_the_wheel(wheel_names):
    cmds = [n for n in wheel_names if n.startswith(f"{PREFIX}/commands/") and n.endswith(".md")]
    assert len(cmds) == 14, sorted(cmds)


def test_skill_is_in_the_wheel(wheel_names):
    assert f"{PREFIX}/skills/conscio/SKILL.md" in wheel_names
