"""v4.6.5 peca 2: o consentimento perdido FALA em vez de ser adivinhado.

O A3 sempre foi falha silenciosa -- a capacidade some e nada avisa. A 4.6.4
trocou o dono do sintoma sem remove-lo: o arg sumiu por desenho e o espaco
nunca foi populado, entao o relay morreu igual. O conserto nao e adivinhar o
consentimento (isso seria ressuscitar o de quem revogou), e sim dizer o que
parece perdido e qual comando o restaura.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = (ROOT / "conscio" / "integrations" / "claude_code" / "assets" /
        "hooks" / "conscio_awareness.py")


def _hook():
    spec = importlib.util.spec_from_file_location("conscio_awareness_t", HOOK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["conscio_awareness_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cache_irmao(raiz: Path, versao: str, args: list) -> Path:
    """Um diretorio de cache de outra versao do plugin, com seu .mcp.json."""
    d = raiz / versao
    d.mkdir(parents=True, exist_ok=True)
    (d / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"conscio": {"command": "conscio-mcp", "args": args}}}))
    return d


def test_a_legacy_arg_with_no_marker_is_announced(tmp_path):
    """O caso real da 4.6.4: cache anterior tinha --enable-relay, o novo nao
    tem, e o espaco nunca recebeu o marcador."""
    cache = tmp_path / "cache"
    _cache_irmao(cache, "4.6.3", ["--storage", "x", "--enable-relay"])
    atual = _cache_irmao(cache, "4.6.5", ["--storage", "x"])
    space = tmp_path / "space"; space.mkdir()
    aviso = _hook().consent_warning(space, atual)
    assert aviso and "relay" in aviso
    assert "conscio capabilities enable relay" in aviso


def test_a_granted_capability_is_not_announced(tmp_path):
    """Marcador presente: nada se perdeu, nada a dizer."""
    from conscio.mcp import capabilities as caps
    cache = tmp_path / "cache"
    _cache_irmao(cache, "4.6.3", ["--storage", "x", "--enable-relay"])
    atual = _cache_irmao(cache, "4.6.5", ["--storage", "x"])
    space = tmp_path / "space"; space.mkdir()
    caps.write_capabilities(space, ["relay"])
    assert _hook().consent_warning(space, atual) is None


def test_no_legacy_consent_anywhere_says_nothing(tmp_path):
    """Instalacao nova: nunca houve capacidade, entao nao ha perda a relatar.
    Avisar aqui treinaria o usuario a ignorar o aviso."""
    cache = tmp_path / "cache"
    atual = _cache_irmao(cache, "4.6.5", ["--storage", "x"])
    space = tmp_path / "space"; space.mkdir()
    assert _hook().consent_warning(space, atual) is None


def test_it_never_raises_on_a_broken_cache(tmp_path):
    """Roda em TODA sessao: um cache corrompido nao pode derrubar o turno."""
    cache = tmp_path / "cache"
    quebrado = cache / "4.6.3"; quebrado.mkdir(parents=True)
    (quebrado / ".mcp.json").write_text("{ nao e json")
    atual = _cache_irmao(cache, "4.6.5", ["--storage", "x"])
    space = tmp_path / "space"; space.mkdir()
    assert _hook().consent_warning(space, atual) is None


# ── v4.6.6: o remedio aponta o espaco que o hook leu ──

def test_the_remedy_names_the_storage_the_hook_read(tmp_path):
    """Sem --storage, o CLI nu grava em ~/.conscio/consciousness -- espaco
    diferente do que o hook acabou de ler. O usuario obedece o aviso e o
    consentimento cai fora."""
    cache = tmp_path / "cache"
    _cache_irmao(cache, "4.6.3", ["--storage", "x", "--enable-relay"])
    atual = _cache_irmao(cache, "4.6.5", ["--storage", "x"])
    space = tmp_path / "space"; space.mkdir()
    aviso = _hook().consent_warning(space, atual)
    assert f"--storage {space}" in aviso


def test_running_the_remedy_closes_the_warning(tmp_path):
    """Criterio 10: um aviso cujo remedio nao fecha o proprio aviso e um loop.

    O comando executado sai DO PROPRIO AVISO. Construir o comando aqui
    provaria que o CLI funciona, nao que o aviso manda o comando certo --
    e era assim que este teste ficava verde com a correcao revertida.
    """
    import shlex
    import subprocess
    import sys

    cache = tmp_path / "cache"
    _cache_irmao(cache, "4.6.3", ["--storage", "x", "--enable-relay"])
    atual = _cache_irmao(cache, "4.6.5", ["--storage", "x"])
    space = tmp_path / "space"; space.mkdir()
    hook = _hook()

    aviso = hook.consent_warning(space, atual)
    assert aviso is not None                       # antes: fala

    # O comando vem do aviso. So o executavel e traduzido para rodar o
    # pacote local; tudo depois do primeiro token e do hook.
    comando = shlex.split(aviso.split("to restore it: ")[1].split("; ")[0])
    assert comando[0] == "conscio"
    r = subprocess.run([sys.executable, "-m", "conscio.cli", *comando[1:]],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    assert hook.consent_warning(space, atual) is None   # depois: cala
