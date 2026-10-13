"""Release guard: the wheel must carry every data file the code reads.

v4.9.2 published a wheel with no model-providers.json, no presets/axes/* and no
presets/voice/*. Nothing failed loudly — the loaders are advisory by design —
so every install ran with no provider table, no voice preset and no axis pack.
The defect was not the missing files but the missing check: a guard that looks
at the source tree cannot see a packaging gap, because in the source tree every
file exists. That is exactly how 4.9.2 stayed green.

So the contract comes from git (what SHOULD ship) and the verdict comes from a
wheel (what DID ship). Set CONSCIO_WHEEL to judge an already-built artifact —
the release job points it at the very wheel it is about to publish, so the gate
never rebuilds a second, different wheel.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Tracked files deliberately not packaged. Every entry needs a written reason:
# an exception nobody can justify is how a data file disappears from a release.
EXCLUDED: dict[str, str] = {}

# The three families that vanished in 4.9.2. They may never be EXCLUDED — an
# install without them degrades silently, which is the failure being guarded.
CRITICAL = (
    "conscio/model-providers.json",
    "conscio/presets/axes/core.json",
    "conscio/presets/voice/coherence-style.md",
)


def tracked_data_files() -> list[str]:
    """What SHOULD ship: non-.py files git tracks under conscio/."""
    if not (ROOT / ".git").exists():
        raise AssertionError(
            "wheel guard needs the git index: without it there is no contract "
            "to measure the wheel against"
        )
    out = subprocess.run(
        ["git", "ls-files", "conscio"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [p for p in out.stdout.splitlines() if p and not p.endswith(".py")]


def _staged_source(dest: Path) -> Path:
    """Copy the tracked tree into dest and build there — never in place.

    A build in the developer's working directory is polluted by leftovers: a
    stale build/ or conscio.egg-info/ still carries files the configuration no
    longer lists, and that made this very guard pass with package-data
    removed. Git is the contract, so the staging area is exactly `git ls-files`.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    for raw in out.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode()
        src = ROOT / rel
        if not src.is_file():      # tracked but deleted from the working tree
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> list[str]:
    """What DID ship: a real wheel's namelist, never the source tree."""
    override = os.environ.get("CONSCIO_WHEEL")
    if override:
        wheel = Path(override)
        if not wheel.is_file():
            raise AssertionError(f"CONSCIO_WHEEL points at no file: {wheel}")
    else:
        stage = _staged_source(tmp_path_factory.mktemp("src"))
        out = tmp_path_factory.mktemp("dist")
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
            cwd=stage,
            check=True,
            capture_output=True,
        )
        wheel = next(out.glob("*.whl"))
    return zipfile.ZipFile(wheel).namelist()


def test_every_tracked_data_file_is_in_the_wheel(wheel_names) -> None:
    missing = [
        p for p in tracked_data_files()
        if p not in wheel_names and p not in EXCLUDED
    ]
    assert not missing, (
        "tracked data files are missing from the wheel — the code reads these "
        f"at runtime and would degrade in silence: {missing}"
    )


def test_critical_data_ships_and_is_never_excluded(wheel_names) -> None:
    blocked = [p for p in CRITICAL if p in EXCLUDED]
    assert not blocked, f"critical files may not be packaging exceptions: {blocked}"
    missing = [p for p in CRITICAL if p not in wheel_names]
    assert not missing, f"critical data missing from the wheel: {missing}"
