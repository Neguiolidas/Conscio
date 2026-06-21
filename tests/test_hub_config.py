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
