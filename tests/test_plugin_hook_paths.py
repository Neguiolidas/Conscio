"""Every path a hook manifest names must exist in what the plugin SHIPS.

The plugin distribution is the `assets/` tree and nothing else: the marketplace
copies it verbatim, and `materialize.py` — which vendors extra files at install
time — never runs on that path. A hook registered against a path that only
`materialize` creates is registered against a path the user will not have.

That is not hypothetical. v4.6.0 shipped `conscio_honesty.py` with its
`hooks.json` entry pointing at `${CLAUDE_PLUGIN_ROOT}/hooks/conscio_honesty_pkg`,
a directory the distribution never carried. The hook ran, failed to import,
exited 0 by design, and recorded nothing — indistinguishable from a healthy
install, which is the same failure v4.0.0 had with capture.
"""
import re
import subprocess
from pathlib import Path

ASSETS = (Path(__file__).resolve().parents[1] / "conscio" / "integrations"
          / "claude_code" / "assets")
HOOKS_JSON = ASSETS / "hooks" / "hooks.json"
PKG_SRC = Path(__file__).resolve().parents[1] / "conscio" / "honesty"
PKG_VENDORED = ASSETS / "hooks" / "conscio_honesty_pkg"

_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)")


def _referenced_paths() -> set[str]:
    text = HOOKS_JSON.read_text(encoding="utf-8")
    return set(_ROOT_REF.findall(text))


def test_hooks_json_references_at_least_the_known_hooks():
    """Guard the guard: a regex that matches nothing would pass everything."""
    refs = _referenced_paths()
    assert len(refs) >= 3, f"expected several ${{CLAUDE_PLUGIN_ROOT}} refs, got {refs}"


def test_every_referenced_path_ships_with_the_plugin():
    missing = [r for r in sorted(_referenced_paths()) if not (ASSETS / r).exists()]
    assert not missing, (
        "hooks.json points at paths the plugin does not ship: " + str(missing)
        + " — materialize.py does not run on the marketplace path")


def test_the_vendored_honesty_package_matches_the_source():
    """Two copies drift on the first fix applied to one side only."""
    src = {p.name: p.read_bytes() for p in PKG_SRC.glob("*.py")}
    dst = {p.name: p.read_bytes() for p in PKG_VENDORED.glob("*.py")}
    assert set(src) == set(dst), (
        f"vendored package differs in file set: "
        f"only in source={sorted(set(src) - set(dst))}, "
        f"only in vendored={sorted(set(dst) - set(src))}")
    drifted = [n for n in src if src[n] != dst[n]]
    assert not drifted, f"vendored copy drifted from conscio/honesty/: {drifted}"


def test_the_vendored_package_is_tracked_by_git():
    """A green check against the working tree proves the disk, not the artifact."""
    files = sorted(p for p in PKG_VENDORED.glob("*.py"))
    assert files, "no vendored package on disk at all"
    for path in files:
        out = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)],
                             capture_output=True, text=True)
        assert out.returncode == 0, f"{path} is untracked: it will not ship"
