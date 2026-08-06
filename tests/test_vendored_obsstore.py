"""The hook runs without the package importable, so obsstore travels with it."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "conscio" / "obsstore.py"
VENDORED = ROOT / "conscio" / "integrations" / "claude_code" / "assets" / "hooks" / "conscio_obsstore.py"

REGEN = f"cp {SOURCE.relative_to(ROOT)} {VENDORED.relative_to(ROOT)}"


def test_vendored_copy_exists():
    assert VENDORED.exists(), f"vendored obsstore is missing; regenerate with:\n  {REGEN}"


def test_vendored_copy_is_byte_identical():
    assert VENDORED.read_bytes() == SOURCE.read_bytes(), (
        f"vendored obsstore drifted from the source; regenerate with:\n  {REGEN}")


def test_vendored_copy_imports_without_the_package(tmp_path):
    """Identical bytes are worthless if those bytes need `conscio` on the path."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        # -I already keeps cwd and user-site out; add only the hook dir, so the
        # stdlib stays reachable and `conscio` does not.
        f"sys.path.insert(0, {str(VENDORED.parent)!r})\n"
        "import conscio_obsstore as o\n"
        "assert 'conscio' not in sys.modules\n"
        "assert o.connect and o.migrate and o.SCHEMA_VERSION\n"
    )
    # cwd=tmp_path so the repo root never leaks onto sys.path
    r = subprocess.run([sys.executable, "-I", str(probe)], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
