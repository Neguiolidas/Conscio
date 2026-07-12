"""conscio-mcp must resolve its model from config.json, not only --model/env.

Production bug: the Claude Code bundle registers ``conscio-mcp`` with only
``--storage`` (no --model, no CONSCIO_MODEL). The server read the model solely
from ``args.model`` / ``CONSCIO_MODEL`` and exited 1 ("no model specified"),
so the MCP server never came up — even though ~/.config/conscio/config.json
had a ``model``. Resolution must mirror the daemon: --model > config > env.
"""
from argparse import Namespace

import pytest

import conscio.mcp.server as srv
import conscio.adapter_config as ac


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CONSCIO_MODEL", raising=False)


def test_resolves_model_from_config_when_no_arg(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {"model": "z-ai/glm-5.2"})
    assert srv._resolve_model(Namespace(model=None)) == "z-ai/glm-5.2"


def test_cli_model_beats_config(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {"model": "glm-5.2"})
    assert srv._resolve_model(Namespace(model="kimi-k2.6")) == "kimi-k2.6"


def test_env_used_when_no_arg_no_config(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {})
    monkeypatch.setenv("CONSCIO_MODEL", "glm-5.1")
    assert srv._resolve_model(Namespace(model=None)) == "glm-5.1"


def test_raises_when_nothing_specified(monkeypatch):
    monkeypatch.setattr(ac, "load_config", lambda: {})
    with pytest.raises(ValueError):
        srv._resolve_model(Namespace(model=None))
