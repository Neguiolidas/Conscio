# tests/test_observatory_engine_free.py
"""Engine-free-by-contract proof for conscio/observatory/.

Static AST scan: the observatory must never reach the engine. We cannot use a
runtime sys.modules check (importing any conscio.* first runs conscio/__init__.py,
which eagerly imports conscio.engine). So we scan each observatory source file's
own import statements.

Guard is direct-import only; transitive imports outside this package are a design
invariant, not an enforced boundary. Scope: the observatory package ONLY — the
MCP state tools are legitimately engine-backed and excluded here."""
import ast
import pathlib

import conscio.observatory as obs

_FORBIDDEN_PREFIXES = ("conscio.engine",)
_PKG_DIR = pathlib.Path(obs.__file__).parent


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):                 # import conscio.engine [as e]
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):           # from conscio.engine import X
            if node.level == 0 and node.module:
                names.add(node.module)
    return names


def test_observatory_sources_do_not_import_engine():
    offenders: dict[str, set[str]] = {}
    for py in sorted(_PKG_DIR.glob("*.py")):
        bad = {m for m in _imported_modules(py)
               if any(m == p or m.startswith(p + ".") for p in _FORBIDDEN_PREFIXES)}
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"observatory imports the engine: {offenders}"
