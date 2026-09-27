# tests/test_adapter_config_keys.py
"""Test chat builder API key resolution behavior prior to and after extraction.

Spec section 4 requirement:
(1) test that pins the chat builder (inline key wins over env; env; absent env -> vault;
vault raising -> "")
(2) extraction of resolve_api_key
The test file must remain unmodified in step 2 (empty git diff).
"""
from __future__ import annotations

import conscio.adapter_config as ac


def test_chat_builder_inline_api_key_wins_over_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "env-key")
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m",
                     "api_key": "raw-key", "api_key_env": "MY_KEY"}},
        fallback_model="m",
    )
    assert t == "openai"
    assert a.api_key == "raw-key"


def test_chat_builder_env_key(monkeypatch):
    monkeypatch.setenv("MY_KEY", "env-key")
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m", "api_key_env": "MY_KEY"}},
        fallback_model="m",
    )
    assert t == "openai"
    assert a.api_key == "env-key"


def test_chat_builder_env_absent_falls_back_to_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSCIO_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("VAULT_KEY", raising=False)
    (tmp_path / "VAULT_KEY").write_text("vault-secret-abc\n")
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m", "api_key_env": "VAULT_KEY"}},
        fallback_model="m",
    )
    assert t == "openai"
    assert a.api_key == "vault-secret-abc"


def test_chat_builder_vault_raising_returns_empty(monkeypatch):
    monkeypatch.delenv("RAISING_KEY", raising=False)
    from conscio.hub import config as hub_config

    def _broken(name: str):
        raise RuntimeError("vault error")

    monkeypatch.setattr(hub_config, "vault_load", _broken)
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m", "api_key_env": "RAISING_KEY"}},
        fallback_model="m",
    )
    assert t == "openai"
    assert a.api_key == ""


def test_chat_builder_no_env_name_and_no_key():
    a, t = ac.build_adapter_from_config(
        {"adapter": {"type": "openai", "model": "m"}},
        fallback_model="m",
    )
    assert t == "openai"
    assert a.api_key == ""
