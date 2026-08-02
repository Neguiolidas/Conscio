"""BUG-38: `HERMES_HOME=~/.hermes` must not resolve to a directory named `~`.

Six sites read the variable straight into a `Path`. The default they fall back
to is `Path.home() / ".hermes"`, which is absolute — so with the variable unset
everything works, and the defect only appears when something in the environment
exported an unexpanded tilde. Then every path silently becomes *relative* to the
working directory, the files that were there are not found, and the layer above
happily creates new ones in the wrong place.

Four of the six are module-level constants, evaluated at import time, so
monkeypatching the environment inside this process would prove nothing. Each
probe is therefore a fresh interpreter that imports the real modules with the
environment already set.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# site id, module, attribute, call args (None = read it, don't call it),
# path below HERMES_HOME the site is expected to produce.
_SITES = [
    ("session_lifecycle.HERMES_HOME", "conscio.session_lifecycle",
     "HERMES_HOME", None, ""),
    ("session_lifecycle.SESSION_DB", "conscio.session_lifecycle",
     "SESSION_DB", None, "state.db"),
    ("session_rag.HERMES_HOME", "conscio.session_rag",
     "HERMES_HOME", None, ""),
    ("session_rag.RAG_DB", "conscio.session_rag",
     "RAG_DB", None, "consciousness/session_rag.db"),
    ("noosphere.paths.hermes_home", "conscio.noosphere.paths",
     "hermes_home", (), ""),
    ("cli._storage", "conscio.cli",
     "_storage", ("",), "consciousness"),
    ("observatory._DEFAULT_NOOSPHERE", "conscio.observatory.server",
     "_DEFAULT_NOOSPHERE", None, "noosphere.db"),
    ("observatory._DEFAULT_LIAISON", "conscio.observatory.server",
     "_DEFAULT_LIAISON", None, "liaison.db"),
]

_PROBE = """
import importlib, json, sys
out = {}
for site, module, attr, args in json.loads(sys.argv[1]):
    value = getattr(importlib.import_module(module), attr)
    out[site] = str(value if args is None else value(*args))
print(json.dumps(out))
"""


def _probe(hermes_home: str | None, home: Path) -> dict[str, str]:
    """Resolve every site in a fresh interpreter under a given environment."""
    # HERMES_HOME is what this test sets; the other two are overrides
    # session_lifecycle applies *after* it, and would mask the result.
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_HOME", "CONSCIO_SESSION_DB",
                        "CONSCIO_HANDOFF_DIR")}
    env["HOME"] = str(home)
    if hermes_home is not None:
        env["HERMES_HOME"] = hermes_home
    table = [[site, module, attr, args] for site, module, attr, args, _ in _SITES]
    done = subprocess.run([sys.executable, "-c", _PROBE, json.dumps(table)],
                          env=env, cwd=_REPO, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _expected(root: Path, rel: str) -> Path:
    return root / rel if rel else root


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return tmp_path_factory.mktemp("home")


@pytest.fixture(scope="module")
def tilde(home):
    return _probe("~/.hermes", home)


@pytest.fixture(scope="module")
def unset(home):
    return _probe(None, home)


@pytest.fixture(scope="module")
def elsewhere(tmp_path_factory):
    return tmp_path_factory.mktemp("explicit-hermes")


@pytest.fixture(scope="module")
def absolute(home, elsewhere):
    return _probe(str(elsewhere), home)


@pytest.mark.parametrize("site, rel",
                         [(s[0], s[4]) for s in _SITES])
def test_a_tilde_from_the_environment_is_expanded(tilde, home, site, rel):
    resolved = tilde[site]
    assert "~" not in resolved, f"{site} kept the tilde literally"
    assert resolved == str(_expected(home / ".hermes", rel))


@pytest.mark.parametrize("site, rel",
                         [(s[0], s[4]) for s in _SITES])
def test_an_unset_variable_still_falls_back_to_the_home_directory(
        unset, home, site, rel):
    """The half that always worked — pinned so the fix cannot break it."""
    assert unset[site] == str(_expected(home / ".hermes", rel))


@pytest.mark.parametrize("site, rel",
                         [(s[0], s[4]) for s in _SITES])
def test_an_absolute_value_is_left_alone(absolute, elsewhere, site, rel):
    assert absolute[site] == str(_expected(elsewhere, rel))
