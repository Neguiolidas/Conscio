# tests/test_packaging.py
"""Packaging contract — single-source version, console scripts, py.typed, zero-dep core."""
import pathlib

import pytest

tomllib = pytest.importorskip("tomllib")          # stdlib 3.11+; CI is 3.11/3.12

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_version_is_single_sourced():
    p = _pyproject()
    assert "version" not in p["project"], "static version must be removed"
    assert p["project"]["dynamic"] == ["version"]
    attr = p["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "conscio.__version__"


def test_console_scripts_declared():
    scripts = _pyproject()["project"]["scripts"]
    assert scripts["conscio"] == "conscio.cli:main"
    assert scripts["conscio-bench"] == "conscio.bench:main"
    assert "conscio-daemon" not in scripts        # reserved for F5, not shipped


def test_py_typed_present_and_packaged():
    assert (ROOT / "conscio" / "py.typed").exists()
    pkgdata = _pyproject()["tool"]["setuptools"]["package-data"]["conscio"]
    assert "py.typed" in pkgdata


def test_docs_extra_is_dev_only():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert any("mkdocs" in dep for dep in extras["docs"])
    # docs tooling must never be a runtime dependency
    assert all("mkdocs" not in dep for dep in _pyproject()["project"]["dependencies"])


def test_core_import_pulls_no_packaging_or_doc_tooling():
    import importlib
    import sys
    for m in ("mkdocs", "build", "twine"):
        sys.modules.pop(m, None)
    importlib.import_module("conscio")
    assert not any(m in sys.modules for m in ("mkdocs", "build", "twine"))


def test_top_level_exports_new_surface():
    import conscio
    for name in ("SensorAdapter", "PerceptionFrame", "MockSensor", "Risk"):
        assert name in conscio.__all__
        assert hasattr(conscio, name)
