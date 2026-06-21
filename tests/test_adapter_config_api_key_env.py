import conscio.adapter_config as ac


def test_api_key_env_resolved(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-123")
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m", "api_key_env": "MY_KEY"}},
        fallback_model="m")
    assert t == "openai" and a.api_key == "secret-123"


def test_raw_api_key_still_wins(monkeypatch):
    monkeypatch.setenv("MY_KEY", "env-key")
    a, _ = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m",
                     "api_key": "raw-key", "api_key_env": "MY_KEY"}},
        fallback_model="m")
    assert a.api_key == "raw-key"          # back-compat: raw key read first


def test_missing_env_is_empty(monkeypatch):
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    a, _ = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m", "api_key_env": "ABSENT_KEY"}},
        fallback_model="m")
    assert a.api_key == ""
