# tests/test_noosphere_identity.py
import pytest
from conscio.noosphere import identity


def test_create_then_stable_across_restarts(tmp_path):
    a = identity.load_or_create(tmp_path)
    b = identity.load_or_create(tmp_path)             # "restart"
    assert a.instance_id == b.instance_id
    assert (tmp_path / "instance.json").exists()


def test_file_mode_is_0600(tmp_path):
    identity.load_or_create(tmp_path)
    mode = (tmp_path / "instance.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_label_override(tmp_path):
    identity.load_or_create(tmp_path)
    out = identity.set_label(tmp_path, "  prod-box  ")
    assert out.label == "prod-box"                    # stripped
    assert identity.load_or_create(tmp_path).label == "prod-box"


def test_label_rejects_empty_and_control_and_long(tmp_path):
    identity.load_or_create(tmp_path)
    for bad in ("", "   ", "a\nb", "x" * 121):
        with pytest.raises(ValueError):
            identity.set_label(tmp_path, bad)


def test_corrupt_file_hard_fails_without_overwrite(tmp_path):
    p = tmp_path / "instance.json"
    p.write_text("{not json")
    with pytest.raises(identity.NoosphereIdentityError):
        identity.load_or_create(tmp_path)
    assert p.read_text() == "{not json"               # untouched
