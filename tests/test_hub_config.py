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
