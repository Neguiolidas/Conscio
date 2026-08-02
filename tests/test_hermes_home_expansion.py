"""BUG-38/38b: environment variables with ``~`` must not resolve to a directory
named ``~``.

Thirteen sites read environment variables straight into a ``Path``. The defaults
they fall back to use ``Path.home()`` which is absolute — so with the variable
unset everything works, and the defect only appears when something in the
environment exported an unexpanded tilde. Then every path silently becomes
*relative* to the working directory, the files that were there are not found,
and the layer above happily creates new ones in the wrong place.

Seven of the thirteen are module-level constants, evaluated at import time, so
monkeypatching the environment inside this process would prove nothing. Each
probe is therefore a fresh interpreter that imports the real modules with the
environment already set.

The original eight sites (BUG-38) read ``HERMES_HOME``; the seven extension
sites (BUG-38b) read ``CONSCIO_SESSION_DB``, ``CONSCIO_HANDOFF_DIR``,
``CONSCIO_BASE``, ``CONSCIO_VAULT_DIR``, ``CONSCIO_WORKSPACE``, ``CLAUDE_DIR``,
``CLAUDE_JSON`` and ``CLAUDE_PROJECT_DIR``.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# All environment variables the test matrix may set — stripped from the host
# environment so a local developer's HERMES_HOME never leaks into the probe.
_ENV_VARS = (
    "HERMES_HOME",
    "CONSCIO_SESSION_DB",
    "CONSCIO_HANDOFF_DIR",
    "CONSCIO_BASE",
    "CONSCIO_VAULT_DIR",
    "CONSCIO_WORKSPACE",
    "CLAUDE_DIR",
    "CLAUDE_JSON",
    "CLAUDE_PROJECT_DIR",
)

_PROBE = """
import importlib, json, sys
out = {}
for site, module, attr, args in json.loads(sys.argv[1]):
    value = getattr(importlib.import_module(module), attr)
    out[site] = str(value if args is None else value(*args))
print(json.dumps(out))
"""

_PROBE_WORKSPACE = """
import json, sys
from conscio.workspace import WorkspaceContext
wc = WorkspaceContext()
out = {"workspace._resolve_root": str(wc._resolve_root())}
print(json.dumps(out))
"""

_PROBE_GOVERNOR_LOCAL = """
import json, sys
from conscio.governor import settings_path
out = {"governor.settings_path_local": str(settings_path("local"))}
print(json.dumps(out))
"""


def _run_probe(script: str, env: dict[str, str], *args: str) -> dict[str, str]:
    """Run a probe script in a fresh interpreter and return its JSON output."""
    done = subprocess.run(
        [sys.executable, "-c", script, *args],
        env=env, cwd=_REPO, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _clean_env(home: Path, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Base env with all testable vars stripped; HOME set; overrides applied."""
    env = {k: v for k, v in os.environ.items() if k not in _ENV_VARS}
    env["HOME"] = str(home)
    if overrides:
        env.update(overrides)
    return env


def _probe_sites(sites: list[tuple], env: dict[str, str]) -> dict[str, str]:
    """Probe a list of (site, module, attr, args) tuples in a fresh interpreter."""
    table = [[s[0], s[1], s[2], s[3]] for s in sites]
    return _run_probe(_PROBE, env, json.dumps(table))


# ── Site definitions ─────────────────────────────────────────────────
# (site_id, module, attr, args, env_var, rel_under_root)
# env_var is the variable this site reads; rel is the path fragment below
# the root that env_var resolves to ("" if the site IS the root).

_HERMES_SITES = [
    ("session_lifecycle.HERMES_HOME", "conscio.session_lifecycle",
     "HERMES_HOME", None, "HERMES_HOME", ""),
    ("session_lifecycle.SESSION_DB", "conscio.session_lifecycle",
     "SESSION_DB", None, "HERMES_HOME", "state.db"),
    ("session_rag.HERMES_HOME", "conscio.session_rag",
     "HERMES_HOME", None, "HERMES_HOME", ""),
    ("session_rag.RAG_DB", "conscio.session_rag",
     "RAG_DB", None, "HERMES_HOME", "consciousness/session_rag.db"),
    ("noosphere.paths.hermes_home", "conscio.noosphere.paths",
     "hermes_home", (), "HERMES_HOME", ""),
    ("cli._storage", "conscio.cli",
     "_storage", ("",), "HERMES_HOME", "consciousness"),
    ("observatory._DEFAULT_NOOSPHERE", "conscio.observatory.server",
     "_DEFAULT_NOOSPHERE", None, "HERMES_HOME", "noosphere.db"),
    ("observatory._DEFAULT_LIAISON", "conscio.observatory.server",
     "_DEFAULT_LIAISON", None, "HERMES_HOME", "liaison.db"),
]

# Each extension site is tested in isolation — only its env var is set, so
# there is no cross-talk between overrides (e.g. CONSCIO_SESSION_DB overriding
# the SESSION_DB that HERMES_HOME derived).
_EXT_SITES = [
    ("session_lifecycle.SESSION_DB_via_env", "conscio.session_lifecycle",
     "SESSION_DB", None, "CONSCIO_SESSION_DB", ""),
    ("session_lifecycle.HANDOFF_DIR_via_env", "conscio.session_lifecycle",
     "HANDOFF_DIR", None, "CONSCIO_HANDOFF_DIR", ""),
    ("installer.spaces._base", "conscio.installer.spaces",
     "_base", (), "CONSCIO_BASE", ""),
    ("hub.config._vault_dir", "conscio.hub.config",
     "_vault_dir", (None,), "CONSCIO_VAULT_DIR", ""),
    ("materialize._claude_dir", "conscio.integrations.claude_code.materialize",
     "_claude_dir", (None,), "CLAUDE_DIR", ""),
    ("materialize.claude_json_path", "conscio.integrations.claude_code.materialize",
     "claude_json_path", (None,), "CLAUDE_JSON", ".claude.json"),
    ("governor.projects_dir", "conscio.governor",
     "projects_dir", (), "CLAUDE_DIR", "projects"),
    ("governor.settings_path_global", "conscio.governor",
     "settings_path", ("global",), "CLAUDE_DIR", "settings.json"),
]


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return tmp_path_factory.mktemp("home")


@pytest.fixture(scope="module")
def elsewhere(tmp_path_factory):
    return tmp_path_factory.mktemp("explicit")


# ── BUG-38: HERMES_HOME sites (all 8 set together, no cross-talk) ─────

@pytest.fixture(scope="module")
def hermes_tilde(home):
    return _probe_sites(_HERMES_SITES, _clean_env(home, {"HERMES_HOME": "~/.hermes"}))

@pytest.fixture(scope="module")
def hermes_unset(home):
    return _probe_sites(_HERMES_SITES, _clean_env(home))

@pytest.fixture(scope="module")
def hermes_absolute(home, elsewhere):
    return _probe_sites(_HERMES_SITES, _clean_env(home, {"HERMES_HOME": str(elsewhere)}))


@pytest.mark.parametrize("site, rel",
                         [(s[0], s[5]) for s in _HERMES_SITES])
def test_hermes_tilde_is_expanded(hermes_tilde, home, site, rel):
    resolved = hermes_tilde[site]
    assert "~" not in resolved, f"{site} kept the tilde literally"
    expected = str(home / ".hermes" / rel) if rel else str(home / ".hermes")
    assert resolved == expected, f"{site}: {resolved} != {expected}"


@pytest.mark.parametrize("site, rel",
                         [(s[0], s[5]) for s in _HERMES_SITES])
def test_hermes_unset_falls_back(hermes_unset, home, site, rel):
    expected = str(home / ".hermes" / rel) if rel else str(home / ".hermes")
    assert hermes_unset[site] == expected, f"{site}: {hermes_unset[site]} != {expected}"


@pytest.mark.parametrize("site, rel",
                         [(s[0], s[5]) for s in _HERMES_SITES])
def test_hermes_absolute_is_left_alone(hermes_absolute, elsewhere, site, rel):
    expected = str(elsewhere / rel) if rel else str(elsewhere)
    assert hermes_absolute[site] == expected, f"{site}: {hermes_absolute[site]} != {expected}"


# ── BUG-38b: Extension sites (each tested in isolation) ──────────────

def _tilde_value(env_var: str, home: Path) -> str:
    """The tilde path this env var should be set to for the test."""
    tilde_paths = {
        "CONSCIO_SESSION_DB": "~/.conscio/session.db",
        "CONSCIO_HANDOFF_DIR": "~/.conscio/handoff",
        "CONSCIO_BASE": "~/.conscio",
        "CONSCIO_VAULT_DIR": "~/.conscio/vault",
        "CLAUDE_DIR": "~/.claude",
        "CLAUDE_JSON": "~/.claude.json",
    }
    return tilde_paths[env_var]


def _expected_tilde(env_var: str, home: Path, rel: str) -> str:
    """Expected resolved path when the env var is set to a tilde path."""
    tilde_roots = {
        "CONSCIO_SESSION_DB": home / ".conscio" / "session.db",
        "CONSCIO_HANDOFF_DIR": home / ".conscio" / "handoff",
        "CONSCIO_BASE": home / ".conscio",
        "CONSCIO_VAULT_DIR": home / ".conscio" / "vault",
        "CLAUDE_DIR": home / ".claude",
        "CLAUDE_JSON": home,
    }
    root = tilde_roots[env_var]
    return str(root / rel) if rel else str(root)


@pytest.mark.parametrize("site, module, attr, args, env_var, rel", _EXT_SITES)
def test_ext_tilde_is_expanded(home, site, module, attr, args, env_var, rel):
    """Each extension site must expand a tilde in its env var."""
    env = _clean_env(home, {env_var: _tilde_value(env_var, home)})
    result = _probe_sites([(site, module, attr, args)], env)
    resolved = result[site]
    assert "~" not in resolved, f"{site} kept the tilde literally"
    assert resolved == _expected_tilde(env_var, home, rel), \
        f"{site}: {resolved} != {_expected_tilde(env_var, home, rel)}"


# ── Special sites: resolve() / cwd dependency ─────────────────────────

def test_conscio_workspace_tilde_is_expanded(home):
    """CONSCIO_WORKSPACE='~/workspace' must not keep the tilde (BUG-38b)."""
    env = _clean_env(home, {"CONSCIO_WORKSPACE": "~/workspace"})
    out = _run_probe(_PROBE_WORKSPACE, env)
    resolved = out["workspace._resolve_root"]
    assert "~" not in resolved, "CONSCIO_WORKSPACE kept the tilde literally"
    assert resolved == str((home / "workspace").resolve())


def test_conscio_workspace_unset_falls_back(home):
    """With CONSCIO_WORKSPACE unset, _resolve_root falls back to git/cwd."""
    env = _clean_env(home)
    out = _run_probe(_PROBE_WORKSPACE, env)
    assert "~" not in out["workspace._resolve_root"]


def test_claude_project_dir_tilde_is_expanded(home):
    """CLAUDE_PROJECT_DIR='~/project' must not keep the tilde (BUG-38b)."""
    env = _clean_env(home, {"CLAUDE_PROJECT_DIR": "~/project"})
    out = _run_probe(_PROBE_GOVERNOR_LOCAL, env)
    resolved = out["governor.settings_path_local"]
    assert "~" not in resolved, "CLAUDE_PROJECT_DIR kept the tilde literally"
    assert resolved == str(
        (home / "project").expanduser() / ".claude" / "settings.local.json")
