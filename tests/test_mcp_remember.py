"""conscio.remember must survive a process-lifetime restart and come back via recall."""
import json

import pytest

from conscio.engine import ConsciousnessEngine
from conscio.mcp import server
from conscio.mcp.schemas import BASE_TOOL_DEFS
from conscio.mcp.seen import SeenStore

SENTINEL = "the kraken sleeps beneath the bergamot pier"


def _bindings(tmp_path, **kw):
    """Mirrors _bindings() in tests/test_mcp_battery.py: seen is positional."""
    engine = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    return server.Bindings(engine, SeenStore(tmp_path / "mcp_seen.db"), **kw), engine


def _call(bindings, name, args):
    return json.loads(bindings.call_tool(name, args)["content"][0]["text"])


def test_remember_is_in_base_tool_defs():
    names = [d["name"] for d in BASE_TOOL_DEFS]
    assert "conscio.remember" in names


def test_remember_requires_text(tmp_path):
    b, engine = _bindings(tmp_path)
    try:
        with pytest.raises(Exception):
            _call(b, "conscio.remember", {})
    finally:
        engine.close()


def test_remember_survives_restart_and_comes_back_through_recall(tmp_path):
    b, first = _bindings(tmp_path)
    try:
        out = _call(b, "conscio.remember", {"text": SENTINEL, "label": "kraken"})
        assert out["stored"] is True
    finally:
        first.close()                      # real restart: new process-lifetime object

    b2, second = _bindings(tmp_path)
    try:
        got = _call(b2, "conscio.recall", {"query": "kraken pier"})
        # Controle de categoria: sem filtro, o FTS5 acha a sentinela pelas
        # palavras distintivas mesmo que remember tenha gravado uma categoria
        # VÁLIDA-mas-errada (ex. "session") — categoria inválida já falha alto
        # no ValueError de content_store.py:235. Este recall filtrado prova a
        # string gravada. (NÃO prova a camada: layer_of() tem default
        # PROCESSING, que é a própria prioridade máxima — indistinguível.)
        got_cat = _call(b2, "conscio.recall",
                        {"query": "kraken pier", "categories": ["consciousness"]})
    finally:
        second.close()

    assert SENTINEL in json.dumps(got), f"sentinel not recalled: {got}"
    assert SENTINEL in json.dumps(got_cat), f"wrong category stored: {got_cat}"
