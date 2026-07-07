# tests/test_mcp_adapter_parity.py
"""Parity lock: the shared builder (used by conscio-mcp via main()) handles all
six daemon provider types. The full main() config wiring is covered by the live
stdio smoke."""
from conscio.adapter_config import build_adapter_from_config


def test_six_provider_types_build():
    for t in ("lmstudio", "ollama", "openai", "anthropic", "gemini",
              "openai-compat"):
        a, atype = build_adapter_from_config(
            {"adapter": {"type": t, "model": "m"}}, fallback_model="m")
        assert atype == t and a is not None


def test_mcp_main_is_importable():
    from conscio.mcp.server import _arg_parser, main
    assert callable(main) and _arg_parser().parse_args(["--model", "glm-5.1"]).model
