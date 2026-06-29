from pathlib import Path
from conscio.installer import extras


def test_registry_only_graphify():
    assert set(extras.REGISTRY) == {"graphify"}


def test_graphify_enable_steps():
    steps = extras.REGISTRY["graphify"].enable(Path("/tmp/space"))
    assert any("graphify" in s for s in steps)
    assert any("consent" in s and "/tmp/space" in s for s in steps)


def test_extra_has_summary_and_no_runtime_dep():
    g = extras.REGISTRY["graphify"]
    assert g.summary
    assert g.optional_dep is None         # graphify is an external CLI, not a pydep
