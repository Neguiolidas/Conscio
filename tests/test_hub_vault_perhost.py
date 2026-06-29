import importlib


def _fresh(monkeypatch, env_val):
    if env_val is None:
        monkeypatch.delenv("CONSCIO_VAULT_DIR", raising=False)
    else:
        monkeypatch.setenv("CONSCIO_VAULT_DIR", str(env_val))
    import conscio.hub.config as cfg
    return importlib.reload(cfg)


def test_default_dir_is_legacy_global(monkeypatch):
    cfg = _fresh(monkeypatch, None)
    from conscio import adapter_config
    assert cfg._vault_dir() == adapter_config._CONFIG_PATHS[0].parent / "keys"


def test_env_overrides_dir(monkeypatch, tmp_path):
    cfg = _fresh(monkeypatch, tmp_path / "space" / "keys")
    assert cfg._vault_dir() == tmp_path / "space" / "keys"


def test_explicit_override_beats_env(monkeypatch, tmp_path):
    cfg = _fresh(monkeypatch, tmp_path / "env_keys")
    assert cfg._vault_dir(tmp_path / "arg_keys") == tmp_path / "arg_keys"


def test_store_then_load_roundtrip_perhost(monkeypatch, tmp_path):
    monkeypatch.delenv("CONSCIO_API_X", raising=False)
    cfg = _fresh(monkeypatch, None)
    vd = tmp_path / "keys"
    cfg.vault_store("CONSCIO_API_X", "sek", vault_dir=vd)
    monkeypatch.delenv("CONSCIO_API_X", raising=False)        # drop env cache
    assert cfg.vault_load("CONSCIO_API_X", vault_dir=vd) == "sek"
    assert (vd / "CONSCIO_API_X").stat().st_mode & 0o777 == 0o600


def test_no_perhost_to_global_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("CONSCIO_API_Y", raising=False)
    cfg = _fresh(monkeypatch, None)
    cfg.vault_store("CONSCIO_API_Y", "global", vault_dir=tmp_path / "g")
    monkeypatch.delenv("CONSCIO_API_Y", raising=False)
    # a different (empty) per-host dir must NOT see the global key
    assert cfg.vault_load("CONSCIO_API_Y", vault_dir=tmp_path / "h") is None
