import pytest
from conscio.installer import spaces


@pytest.fixture(autouse=True)
def _base(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSCIO_BASE", str(tmp_path / ".conscio"))


def test_slugify_normalizes():
    assert spaces.slugify("Claude Code @ Laptop!") == "claude-code-laptop"
    assert spaces.slugify("  a__b  ") == "a-b"


def test_ensure_space_creates_then_reuses():
    d1, id1, created1 = spaces.ensure_space("claude-code")
    assert created1 is True
    assert d1.is_dir() and (d1 / "instance.json").is_file()
    assert (spaces.vault_dir("claude-code")) == d1 / "keys"
    d2, id2, created2 = spaces.ensure_space("claude-code")
    assert created2 is False
    assert id2.instance_id == id1.instance_id          # identity preserved


def test_distinct_slugs_distinct_identity():
    _, a, _ = spaces.ensure_space("host-a")
    _, b, _ = spaces.ensure_space("host-b")
    assert a.instance_id != b.instance_id
