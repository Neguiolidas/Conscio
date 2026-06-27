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


def test_api_key_falls_back_to_vault(tmp_path, monkeypatch):
    """The Hub stores keys in the vault file (~/.config/conscio/keys/<NAME>),
    never in the environment. When the env var is unset, the adapter builder
    MUST read the vault — otherwise the daemon/MCP/CLI build a keyless adapter
    and every authed provider returns 401."""
    from conscio.hub import config as hubcfg
    monkeypatch.setattr(hubcfg, "_VAULT_DIR", tmp_path)
    monkeypatch.delenv("VAULT_ONLY_KEY", raising=False)
    (tmp_path / "VAULT_ONLY_KEY").write_text("vault-secret-xyz\n")
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m",
                     "api_key_env": "VAULT_ONLY_KEY"}},
        fallback_model="m")
    assert t == "openai"
    assert a.api_key == "vault-secret-xyz"        # stripped, loaded from vault


def test_env_still_wins_over_vault(tmp_path, monkeypatch):
    """Env var present -> used directly; the vault is only a fallback."""
    from conscio.hub import config as hubcfg
    monkeypatch.setattr(hubcfg, "_VAULT_DIR", tmp_path)
    monkeypatch.setenv("BOTH_KEY", "env-wins")
    (tmp_path / "BOTH_KEY").write_text("vault-loses\n")
    a, _ = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m", "api_key_env": "BOTH_KEY"}},
        fallback_model="m")
    assert a.api_key == "env-wins"
