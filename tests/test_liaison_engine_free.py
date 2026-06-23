# tests/test_liaison_engine_free.py
"""Engine-free-by-contract proof for conscio/liaison/ (mirrors the observatory
guard). Static AST scan of each source file's own import statements; the liaison
package must never import conscio.engine."""
import ast
import pathlib

import conscio.liaison as lia

_FORBIDDEN_PREFIXES = ("conscio.engine",)
_PKG_DIR = pathlib.Path(lia.__file__).parent


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
    return names


def test_liaison_sources_do_not_import_engine():
    offenders: dict[str, set[str]] = {}
    for py in sorted(_PKG_DIR.glob("*.py")):
        bad = {m for m in _imported_modules(py)
               if any(m == p or m.startswith(p + ".") for p in _FORBIDDEN_PREFIXES)}
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"liaison imports the engine: {offenders}"
