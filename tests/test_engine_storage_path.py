"""Where a ConsciousnessEngine puts its storage.

A path is what the caller meant by it, not the characters they typed: a
`"~/…"` string used to create a directory literally named `~` in the working
directory and store a mind inside it.
"""
from conscio.engine import ConsciousnessEngine


def test_tilde_is_expanded_not_taken_literally(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path="~/conscio-live")
    try:
        assert "~" not in str(eng.storage)
        assert eng.storage == tmp_path / "conscio-live"
        assert eng.storage.is_dir()
    finally:
        eng.close()


def test_an_explicit_path_is_left_alone(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path / "s")
    try:
        assert eng.storage == tmp_path / "s"
    finally:
        eng.close()
