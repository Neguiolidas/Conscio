import pathlib
import pytest

tomllib = pytest.importorskip("tomllib")


def test_integration_assets_in_package_data():
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    globs = data["tool"]["setuptools"]["package-data"]["conscio"]
    assert any("integrations/claude_code/assets" in g for g in globs)
