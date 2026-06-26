import json
import conscio.adapter_config as ac
from conscio.hub import config


def test_load_reads_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"model": "glm-5.1"}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    assert config.load()["model"] == "glm-5.1"


def test_config_path_defaults_to_first_when_absent(tmp_path, monkeypatch):
    p = tmp_path / "nope.json"
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    assert config.config_path() == p


def test_validate_ok():
    cfg = {"model": "m", "adapter": {"type": "openai", "api_key_env": "OK_KEY"}}
    assert config.validate(cfg) == []


def test_validate_rejects_empty_model():
    assert config.validate({"model": "", "adapter": {"type": "openai"}})


def test_validate_rejects_unknown_type():
    assert config.validate({"model": "m", "adapter": {"type": "bogus"}})


def test_validate_rejects_raw_key_in_env_field():
    # a pasted raw key (lowercase + dashes) must fail the env-NAME regex
    errs = config.validate(
        {"model": "m", "adapter": {"type": "openai", "api_key_env": "sk-abc-123"}})
    assert errs


def test_validate_checks_provider_env_names():
    errs = config.validate(
        {"model": "m", "adapter": {"type": "openai"},
         "providers": {"x": {"type": "openai", "api_key_env": "bad-name"}}})
    assert errs


def test_redact_drops_raw_key(monkeypatch):
    out = config.redact({"adapter": {"type": "openai", "api_key": "raw-secret"}})
    assert "api_key" not in out["adapter"]


def test_redact_masks_env_with_presence(monkeypatch):
    monkeypatch.setenv("PRESENT_KEY", "v")
    out = config.redact({"adapter": {"type": "openai", "api_key_env": "PRESENT_KEY"}})
    assert out["adapter"]["api_key_env"] == "PRESENT_KEY"
    assert out["adapter"]["api_key_present"] is True


def test_redact_recurses_providers():
    out = config.redact(
        {"adapter": {"type": "openai"},
         "providers": {"x": {"type": "openai", "api_key": "leak"}}})
    assert "api_key" not in out["providers"]["x"]


def test_save_round_trip(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    config.save({"model": "m", "adapter": {"type": "openai"}})
    assert json.loads(p.read_text())["model"] == "m"


def test_save_rejects_invalid(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    try:
        config.save({"model": "", "adapter": {"type": "bogus"}})
        assert False, "should have raised"
    except ValueError:
        assert not p.exists()           # no write on invalid


def test_save_mode_0600(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    config.save({"model": "m", "adapter": {"type": "openai"}})
    assert (p.stat().st_mode & 0o777) == 0o600


def test_validate_and_redact_handle_null_providers():
    cfg = {"model": "m", "adapter": {"type": "openai"}, "providers": None}
    assert config.validate(cfg) == []          # null providers = no providers
    out = config.redact(cfg)
    assert out["providers"] is None            # passes through untouched, no crash


def test_validate_rejects_raw_api_key_field():
    errs = config.validate(
        {"model": "m", "adapter": {"type": "openai", "api_key": "sk-live-x"}})
    assert errs


def test_validate_rejects_file_scheme_base_url():
    # file:// would let probe_models urlopen a local path — block at validate
    errs = config.validate(
        {"model": "m",
         "adapter": {"type": "openai", "base_url": "file:///etc/passwd"}})
    assert errs


def test_validate_rejects_base_url_without_host():
    errs = config.validate(
        {"model": "m", "adapter": {"type": "openai", "base_url": "http:///models"}})
    assert errs


def test_validate_rejects_base_url_with_credentials():
    errs = config.validate(
        {"model": "m",
         "adapter": {"type": "openai", "base_url": "http://user:pass@h/v1"}})
    assert errs


def test_validate_allows_http_and_https_base_url():
    assert config.validate(
        {"model": "m",
         "adapter": {"type": "openai", "base_url": "http://localhost:1234/v1"}}) == []
    assert config.validate(
        {"model": "m",
         "adapter": {"type": "openai", "base_url": "https://api.x/v1"}}) == []


# ── Key vault (v2.7.1) ─────────────────────────────────────────────
import os
import stat

import pytest


def test_env_name_for_sanitizes_traversal():
    n = config._env_name_for("../../etc", "gpt/4o..x")
    assert "/" not in n and ".." not in n
    assert config._valid_env_name(n)


def test_vault_store_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_VAULT_DIR", tmp_path / "keys")
    monkeypatch.delenv("CONSCIO_KEY_OPENAI", raising=False)
    config.vault_store("CONSCIO_KEY_OPENAI", "sk-secret")
    assert config.vault_load("CONSCIO_KEY_OPENAI") == "sk-secret"


def test_vault_file_is_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_VAULT_DIR", tmp_path / "keys")
    config.vault_store("CONSCIO_KEY_X", "k")
    mode = stat.S_IMODE((tmp_path / "keys" / "CONSCIO_KEY_X").stat().st_mode)
    assert mode == 0o600


def test_vault_dir_is_0700(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_VAULT_DIR", tmp_path / "keys")
    config.vault_store("CONSCIO_KEY_X", "k")
    mode = stat.S_IMODE((tmp_path / "keys").stat().st_mode)
    assert mode == 0o700


def test_vault_store_rejects_bad_name(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_VAULT_DIR", tmp_path / "keys")
    with pytest.raises(ValueError):
        config.vault_store("../escape", "k")


def test_vault_has_no_env_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_VAULT_DIR", tmp_path / "keys")
    monkeypatch.delenv("CONSCIO_KEY_Y", raising=False)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "CONSCIO_KEY_Y").write_text("v")
    assert config.vault_has("CONSCIO_KEY_Y") is True
    assert "CONSCIO_KEY_Y" not in os.environ   # presence check must not cache
