import logging
from pathlib import Path

from conscio.installer import binding


def test_none_storage_is_ok():
    assert binding.validate_binding(None) is True


def test_missing_dir_is_bootstrapped_silently(tmp_path, caplog):
    target = tmp_path / "fresh-space"
    with caplog.at_level(logging.WARNING):
        ok = binding.validate_binding(str(target))
    assert ok is True
    assert (target / "instance.json").exists()
    assert "conscio init --repair" not in caplog.text


def test_tilde_storage_is_expanded_not_created_literally(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)           # a literal `~` would land here
    ok = binding.validate_binding("~/tilde-space")
    assert ok is True
    assert (tmp_path / "tilde-space" / "instance.json").exists()
    assert not (Path.cwd() / "~").exists()


def test_dir_without_identity_warns(tmp_path, caplog):
    (tmp_path / "space").mkdir()
    with caplog.at_level(logging.WARNING):
        ok = binding.validate_binding(tmp_path / "space")
    assert ok is False


def test_healthy_binding_ok(tmp_path):
    sp = tmp_path / "space"
    sp.mkdir()
    (sp / "instance.json").write_text("{}")
    assert binding.validate_binding(sp) is True


def test_never_raises_on_garbage():
    assert binding.validate_binding(12345) is True   # unusable -> treated as ok
