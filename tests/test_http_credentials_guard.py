"""Architectural guard: no credentialed module may reach a
redirect-following HTTP client.

R1 is ANY attribute whose name is an opener, R2 is ANY bare name that is
an opener (each narrowing opens holes the canonical-form mutations do
not cover).

A module is **credentialed** when it carries a string constant whose
content matches ``(?i)^(authorization|[a-z-]*api-key)$`` — i.e. it names
an auth header or an api-key header outright.

An **opener** is any urllib entry point that follows redirects by
default: urlopen, urlretrieve, build_opener, OpenerDirector, URLopener,
FancyURLopener. build_opener() installs HTTPRedirectHandler unless told
otherwise, so it is the same leak as urlopen under another name.

Four violation routes in a credentialed module:
  R1 attribute reference whose attr is an opener (any chain:
     ``urllib.request.urlopen``, ``request.urlopen``, ``ur.urlopen``)
  R2 bare name that is an opener, imported from urllib.request
     (``from urllib.request import urlopen`` then ``urlopen(...)``)
  R3 ``from urllib.request import <opener> [as anything]`` — the import
     itself is the violation, an alias cannot hide it
  R4 any import of requests/httpx/aiohttp/urllib3 (import X, import X as
     Y, import X.sub, from X import …, from X.sub import …)

http.client is allowed: it never follows redirects.

A module that fails to parse is a TEST FAILURE, never a skip: a module we
cannot read is a module we cannot guard.

Measured (Claude, AST, on HEAD): 227 modules, 8 credentialed; the
attribute route flags agency/adapters.py:191, decision_adapter.py:100,
liaison/relay_net.py:209 and session_rag.py:237+287; on the fixed tree
every route flags nothing. Zero false positives from the literal reading,
so there is no reason to narrow.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CONSCIO_ROOT = Path(__file__).resolve().parents[1] / "conscio"

OPENER_NAMES = frozenset({
    "urlopen", "urlretrieve", "build_opener",
    "OpenerDirector", "URLopener", "FancyURLopener",
})

THIRD_PARTY_HTTP = frozenset({"requests", "httpx", "aiohttp", "urllib3"})

CREDENTIALED_RE = re.compile(r"^(authorization|[a-z-]*api-key)$", re.IGNORECASE)

REQUIRED_CREDENTIALED = (
    "agency/adapters.py",
    "decision_adapter.py",
    "hub/providers.py",
    "liaison/relay_net.py",
    "session_rag.py",
)

# The five original leak lines on HEAD, as literal fixture snippets (CI may
# clone shallow, so git show is not available there). Each MUST be flagged.
HEAD_PIN_SNIPPETS = (
    # agency/adapters.py:191 — attribute chain
    'AUTH_HEADER = "authorization"\nimport urllib.request\ndef f(url):\n    with urllib.request.urlopen(url, timeout=1) as r:\n        return r.read()\n',
    # decision_adapter.py:100 — attribute chain, call form
    'AUTH_HEADER = "authorization"\nimport urllib.request\ndef f(url):\n    return urllib.request.urlopen(url, timeout=1)\n',
    # liaison/relay_net.py:42+209 — from-import of the submodule + attribute
    'AUTH_HEADER = "api-key"\nfrom urllib import request\ndef f(url):\n    with request.urlopen(url, timeout=1) as resp:\n        return resp.read()\n',
    # session_rag.py:237 — attribute chain
    'AUTH_HEADER = "api-key"\nimport urllib.request\ndef f(url):\n    with urllib.request.urlopen(url, timeout=30) as resp:\n        return resp.read()\n',
    # session_rag.py:287 — same file, second site
    'AUTH_HEADER = "api-key"\nimport urllib.request\ndef post(url, data):\n    req = urllib.request.Request(url, data=data)\n    with urllib.request.urlopen(req, timeout=30) as resp:\n        return resp.read()\n',
)

# The 9 hole variants the QA fed the narrowed guard, plus the third-party
# imports and the opener constructors — each MUST be flagged.
MUST_FLAG_SNIPPETS = (
    # R1 variants (attribute chains)
    'AUTH = "authorization"\nimport urllib.request as ur\ndef f(u):\n    return ur.urlopen(u)\n\n',
    'AUTH = "api-key"\nfrom urllib import request\ndef f(u):\n    return request.urlopen(u)\n\n',
    'AUTH = "api-key"\nfrom urllib import request as rq\ndef f(u):\n    return rq.urlopen(u)\n\n',
    'AUTH = "authorization"\nimport urllib.request\ndef f(u):\n    return urllib.request.build_opener()\n\n',
    'AUTH = "authorization"\nfrom urllib.request import urlopen as f2\n\n',
    # R2 variants (bare names)
    'AUTH = "authorization"\nfrom urllib.request import urlopen\ndef f(u):\n    return urlopen(u)\n\n',
    'AUTH = "authorization"\nfrom .compat import urlopen\ndef f(u):\n    return urlopen(u)\n\n',
    # R4 variants (third-party clients)
    'AUTH = "authorization"\nimport requests\n\n',
    'AUTH = "api-key"\nimport requests as reqs\n\n',
    'AUTH = "api-key"\nimport requests.sessions\n\n',
    'AUTH = "authorization"\nfrom requests import Session\n\n',
    'AUTH = "api-key"\nfrom requests.sessions import Session\n\n',
    'AUTH = "authorization"\nimport httpx\n\n',
    'AUTH = "authorization"\nimport aiohttp\n\n',
    'AUTH = "authorization"\nimport urllib3\n\n',
    # urlretrieve / URLopener / FancyURLopener / OpenerDirector
    'AUTH = "authorization"\nfrom urllib.request import urlretrieve\n\n',
    'AUTH = "api-key"\nfrom urllib.request import URLopener\n\n',
    'AUTH = "authorization"\nfrom urllib.request import FancyURLopener\n\n',
    'AUTH = "authorization"\nfrom urllib.request import OpenerDirector\n\n',
)

# A non-violating twin for every shape that must stay green.
MUST_NOT_FLAG_SNIPPETS = (
    'from urllib.request import Request\ndef f(u):\n    return Request(u)\n',
    'from urllib.parse import urlsplit\ndef f(u):\n    return urlsplit(u)\n',
    'import http.client\ndef f(u):\n    return http.client.HTTPConnection(u)\n',
    'from conscio.http_safety import open_no_redirect\ndef f(u):\n    return open_no_redirect(u)\n',
    'import json\ndef f(d):\n    return json.loads(d)\n',
)


def _credentialed_and_flagged(source: str) -> tuple[bool, list[str]]:
    """(is_credentialed, violation-labels) for one in-memory module source."""
    tree = ast.parse(source)
    credentialed = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if CREDENTIALED_RE.match(node.value.strip()):
                credentialed = True
                break
    if not credentialed:
        return False, []

    labels: list[str] = []
    for node in ast.walk(tree):
        # R3: from urllib.request import <opener> [as anything]
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "urllib.request":
                for alias in node.names:
                    if alias.name in OPENER_NAMES:
                        labels.append(
                            f"R3 from urllib.request import {alias.name}")
            root = module.split(".", 1)[0]
            if root in THIRD_PARTY_HTTP:
                labels.append(f"R4 import {module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in THIRD_PARTY_HTTP:
                    labels.append(f"R4 import {alias.name}")
        # R1 (literal spec): ANY attribute whose attr is an opener.
        elif isinstance(node, ast.Attribute) and node.attr in OPENER_NAMES:
            labels.append(f"R1 attribute .{node.attr}")
        # R2 (literal spec): ANY bare name that is an opener.
        elif isinstance(node, ast.Name) and node.id in OPENER_NAMES:
            labels.append(f"R2 bare {node.id}")

    return True, labels


def _scan(root: Path) -> dict[str, list[str]]:
    """Scan every .py under root for guard violations.

    Returns {module-path: [violation labels]} for credentialed modules only.
    Raises AssertionError on an unparseable module: one we cannot read is
    one we cannot guard.
    """
    found: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise AssertionError(
                f"unparseable module {rel}: {exc}") from exc
        credentialed, labels = _credentialed_and_flagged(
            p.read_text(encoding="utf-8"))
        if credentialed:
            found[rel] = labels
    return found


def test_guard_scan_is_real_and_finds_known_credentialed_set() -> None:
    """The scan must see >100 modules AND the known credentialed set —
    a credential regex that matches nothing would pass while checking
    nothing."""
    files = list(CONSCIO_ROOT.rglob("*.py"))
    assert len(files) > 100, (
        f"guard scanned only {len(files)} modules — the scan is broken, "
        "not the code")

    found = _scan(CONSCIO_ROOT)

    for required in REQUIRED_CREDENTIALED:
        assert required in found, (
            f"guard did not find credentialed module {required!r} — the "
            "credential regex matches nothing; the guard checks nothing")

    leaks = {mod: labels for mod, labels in found.items() if labels}
    assert not leaks, (
        "credentialed modules reaching redirect-following clients: "
        + "; ".join(f"{m}: {v}" for m, v in leaks.items()))


@pytest.mark.parametrize("snippet", HEAD_PIN_SNIPPETS,
                         ids=[f"head-pin-{i}" for i in range(len(HEAD_PIN_SNIPPETS))])
def test_head_leak_lines_are_flagged(snippet: str) -> None:
    """Each of the 5 original HEAD leak shapes MUST be flagged (permanent
    teeth: the guard cannot silently narrow again)."""
    credentialed, labels = _credentialed_and_flagged(snippet)
    assert credentialed, "fixture snippet must be credentialed (has auth constant)"
    assert labels, "this HEAD leak shape must be flagged"


@pytest.mark.parametrize("snippet", MUST_FLAG_SNIPPETS,
                         ids=[f"must-flag-{i}" for i in range(len(MUST_FLAG_SNIPPETS))])
def test_hole_variants_are_flagged(snippet: str) -> None:
    credentialed, labels = _credentialed_and_flagged(snippet)
    assert credentialed, "fixture snippet must be credentialed (has auth constant)"
    assert labels, "this redirect-following shape must be flagged"


@pytest.mark.parametrize("snippet", MUST_NOT_FLAG_SNIPPETS,
                         ids=[f"must-not-{i}" for i in range(len(MUST_NOT_FLAG_SNIPPETS))])
def test_safe_shapes_stay_clean(snippet: str) -> None:
    """The safe twins stay clean: no opener reached, no leak flagged."""
    credentialed, labels = _credentialed_and_flagged(snippet)
    if credentialed:
        assert not labels, f"safe shape flagged: {labels}"


def test_unparseable_module_fails_loud(tmp_path) -> None:
    """A module that fails to parse is a TEST FAILURE, never a skip —
    and the probe module lives on tmp_path, never inside the package
    (a crash in the middle must not ship a broken module)."""
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="unparseable module"):
        _scan(tmp_path)
