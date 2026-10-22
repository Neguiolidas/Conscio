"""Every conscio.<name> an asset mentions must be a tool the server can serve."""
import importlib.util
import re
from pathlib import Path

from conscio.mcp.schemas import (
    ACT_TOOL_DEFS,
    BASE_TOOL_DEFS,
    LIAISON_TOOL_DEFS,
    MODE_TOOL_DEF,
    RELAY_TOOL_DEFS,
)

ASSETS = Path(__file__).resolve().parents[1] / "conscio" / "integrations" / "claude_code" / "assets"

KNOWN = {d["name"] for group in (BASE_TOOL_DEFS, ACT_TOOL_DEFS,
                                 LIAISON_TOOL_DEFS, RELAY_TOOL_DEFS)
         for d in group} | {MODE_TOOL_DEF["name"]}

REF = re.compile(r"\bconscio\.[a-z_]\w*")

# `conscio.<nome>` é ambíguo: é como uma tool MCP se chama E como um módulo
# Python se importa. A regra depende de ONDE a citação aparece, porque ali as
# duas leituras não são equivalentes:
#
# - Num .md (commands/*.md, skills/**/*.md) a citação nomeia uma FERRAMENTA.
#   Aí a regra é a antiga e estrita: a tool precisa existir (KNOWN), e os
#   módulos citados são uma lista à mão (MODULE_REFS). find_spec NUNCA vale
#   para .md: os 92 módulos do pacote dariam cobertura a uma tool fantasma
#   com nome de módulo (`conscio.state` numa skill tem de ser uma tool).
#
# - Num .py (hooks/*.py) a citação é um IMPORT. Aí o módulo precisa existir
#   de verdade — se não, o hook quebra — e a sondagem é find_spec, NUNCA
#   import: importar o módulo para checá-lo executaria o código dele
#   (medido: a sonda de import custa caro demais para um guarda que roda em
#   toda a suíte).
def _is_module(ref: str) -> bool:
    """True when `ref` names an importable module of this package."""
    try:
        return importlib.util.find_spec(ref) is not None
    except (ImportError, ValueError):
        return False


# Módulos citados num .md: cada um entra aqui à mão. Um módulo novo citado
# num .md QUEBRA o teste de propósito — obriga um humano a decidir a qual dos
# dois namespaces aquilo pertence.
MODULE_REFS = {"conscio.timeutil", "conscio.liaison"}

# Terceiro namespace que colide com o mesmo padrão: NOME DE ARQUIVO. O banco do
# espaço chama-se `conscio.db`, e citá-lo é obrigatório em qualquer texto que
# explique onde uma coisa é gravada — a skill de relay cita justamente para
# dizer que `conscio_remember` NÃO escreve nele. Não é um módulo: continua
# listado à mão.
FILE_REFS = {"conscio.db"}


def _phantom_refs(text: str, *, markdown: bool) -> list[str]:
    """The conscio.<name> citations in `text` that name nothing real.

    In a .md a citation names a tool (the strict rule); in a .py it names an
    import (find_spec decides).
    """
    allowed = KNOWN | FILE_REFS
    if markdown:
        allowed = allowed | MODULE_REFS
    else:
        allowed = allowed | {ref for ref in REF.findall(text) if _is_module(ref)}
    return sorted({ref for ref in REF.findall(text) if ref not in allowed})


def _asset_files():
    return (sorted((ASSETS / "commands").glob("*.md"))
            + sorted((ASSETS / "skills").rglob("*.md"))
            + sorted((ASSETS / "hooks").rglob("*.py")))


def test_every_hook_file_on_disk_is_scanned():
    """The guard must read the hooks' package, not only its first level: the
    seven .py under conscio_honesty_pkg were never scanned (Hermet #2770)."""
    on_disk = {p.name for p in (ASSETS / "hooks").rglob("*.py")}
    scanned = {p.name for p in _asset_files() if p.suffix == ".py"}
    assert on_disk <= scanned, sorted(on_disk - scanned)


def test_no_phantom_tool_references():
    phantoms = {}
    for path in _asset_files():
        bad = _phantom_refs(path.read_text("utf-8"),
                            markdown=path.suffix == ".md")
        if bad:
            phantoms[path.name] = bad
    assert not phantoms, f"assets cite tools that do not exist: {phantoms}"


def test_a_real_module_is_not_a_phantom_but_an_invented_name_still_is():
    """In a hook a citation is an import: the module that exists passes, and
    a name that exists neither as a tool nor as a module stays a phantom."""
    assert _phantom_refs("from conscio.installer.durable import resolve_space",
                         markdown=False) == []
    assert _phantom_refs("run conscio.no_such_module_xyz now",
                         markdown=False) == ["conscio.no_such_module_xyz"]


def test_a_module_cited_in_a_markdown_is_a_phantom_there():
    """In a .md a citation names a TOOL: a real module that is not a tool is
    still a phantom there — the 92 module names must not cover a tool
    invented after them."""
    assert _phantom_refs("call conscio.installer now", markdown=True) == [
        "conscio.installer"]
    assert _phantom_refs("call conscio.no_such_module_xyz now",
                         markdown=True) == ["conscio.no_such_module_xyz"]


def test_stack_dependent_commands_explain_instead_of_failing():
    for name in ("awake", "sleep", "society"):
        text = (ASSETS / "commands" / f"{name}.md").read_text("utf-8")
        assert "command -v conscio" in text, f"{name}.md has no binary guard"
        assert "pipx install conscio" in text, f"{name}.md does not say how to fix it"

    relay = (ASSETS / "commands" / "relay.md").read_text("utf-8")
    assert "conscio_relay_send" in relay
    assert "pipx install conscio" in relay


def test_skill_only_cites_tools_available_in_balanced():
    from conscio.mcp import modes
    text = (ASSETS / "skills" / "conscio" / "SKILL.md").read_text("utf-8")
    cited = set(REF.findall(text))
    outside = (cited - modes.BALANCED_TOOLS - {MODE_TOOL_DEF["name"]}
               - MODULE_REFS - FILE_REFS)
    assert not outside, f"SKILL.md cites tools absent from the plugin's default mode: {sorted(outside)}"
