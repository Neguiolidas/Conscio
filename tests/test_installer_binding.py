import logging
from conscio.installer import binding


def test_none_storage_is_ok():
    assert binding.validate_binding(None) is True


def test_missing_dir_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        ok = binding.validate_binding(tmp_path / "nope")
    assert ok is False
    assert "conscio init --repair" in caplog.text


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
