# tests/test_noosphere_engine_free.py
"""Engine-free-by-contract proof.

We cannot use a runtime sys.modules check: importing any conscio.noosphere.*
submodule first runs the parent conscio/__init__.py, which eagerly imports
conscio.engine (and conscio.agency, which pulls SkillLibrary/ToolRegistry).
That parent eagerness is unrelated to whether the NOOSPHERE'S OWN code depends
on the engine. So we verify the contract statically: scan each noosphere source
file's import statements and assert none reference the forbidden modules. The
only conscio.agency.* import allowed is the goal_fingerprint leaf."""
import ast
import pathlib

import conscio.noosphere as noo

_FORBIDDEN_PREFIXES = (
    "conscio.engine",
    "conscio.agency.skills",
    "conscio.agency.tools",
)
_ALLOWED_AGENCY = {"conscio.agency.fingerprint"}

_PKG_DIR = pathlib.Path(noo.__file__).parent


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Absolute module names imported by a source file (relative imports, which
    are intra-package, are ignored — they cannot reach the engine)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:        # absolute import only
                names.add(node.module)
    return names


def test_noosphere_sources_do_not_import_engine_skills_or_tools():
    offenders: dict[str, set[str]] = {}
    for py in sorted(_PKG_DIR.glob("*.py")):
        bad = {m for m in _imported_modules(py)
               if any(m == p or m.startswith(p + ".") for p in _FORBIDDEN_PREFIXES)}
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"noosphere imports forbidden modules: {offenders}"


def test_only_agency_import_is_the_fingerprint_leaf():
    for py in sorted(_PKG_DIR.glob("*.py")):
        agency = {m for m in _imported_modules(py)
                  if m == "conscio.agency" or m.startswith("conscio.agency.")}
        assert agency <= _ALLOWED_AGENCY, (
            f"{py.name} imports disallowed agency modules: {agency - _ALLOWED_AGENCY}")
