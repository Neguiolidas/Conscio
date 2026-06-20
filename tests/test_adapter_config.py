# tests/test_adapter_config.py
from conscio.adapter_config import build_adapter_from_config


def test_builds_openai_compat():
    cfg = {"adapter": {"type": "openai-compat", "model": "glm-5.1",
                       "base_url": "http://x/v1"}}
    adapter, atype = build_adapter_from_config(cfg, fallback_model="m")
    assert atype == "openai-compat"
    assert adapter is not None


def test_no_adapter_block_returns_none():
    a, t = build_adapter_from_config({}, fallback_model="m")
    assert a is None and t is None


def test_unknown_type_returns_none():
    a, t = build_adapter_from_config({"adapter": {"type": "bogus"}},
                                     fallback_model="m")
    assert a is None and t is None
