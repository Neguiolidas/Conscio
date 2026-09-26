# tests/test_ambient_engine_free.py
"""conscio/ambient/ lives inside the reactor tick: it must never pull the engine."""
import ast
import pathlib

import conscio.ambient as amb

_FORBIDDEN = ("conscio.engine",)


def _imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 0:
                names.add(node.module)
            elif node.module == "engine" or node.module.startswith("engine."):
                names.add("conscio.engine")
    return names


def test_ambient_sources_do_not_import_engine():
    bad = {}
    for py in sorted(pathlib.Path(amb.__file__).parent.glob("*.py")):
        hit = {m for m in _imports(py)
               if any(m == p or m.startswith(p + ".") for p in _FORBIDDEN)}
        if hit:
            bad[py.name] = hit
    assert not bad, f"ambient imports the engine: {bad}"
