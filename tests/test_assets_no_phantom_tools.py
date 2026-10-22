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
# Python se importa. Citar um módulo numa docstring é legítimo, então um
# `conscio.<x>` citado num asset passa quando `conscio.<x>` é um módulo que
# existe. A sondagem é find_spec, NUNCA import: importar o módulo para
# checá-lo executaria o código dele (medido: a sonda de import custa caro
# demais para um guarda que roda em toda a suíte). Consequência aceita: um
# nome de tool inventado que coincida com um módulo real passaria — o
# guarda refusa o que não existe nem como tool nem como módulo.
def _is_module(ref: str) -> bool:
    """True when `ref` names an importable module of this package."""
    try:
        return importlib.util.find_spec(ref) is not None
    except (ImportError, ValueError):
        return False


# Terceiro namespace que colide com o mesmo padrão: NOME DE ARQUIVO. O banco do
# espaço chama-se `conscio.db`, e citá-lo é obrigatório em qualquer texto que
# explique onde uma coisa é gravada — a skill de relay cita justamente para
# dizer que `conscio_remember` NÃO escreve nele. Não é um módulo: continua
# listado à mão.
FILE_REFS = {"conscio.db"}


def _phantom_refs(text: str) -> list[str]:
    """The conscio.<name> citations in `text` that name neither a tool the
    server serves nor a module that exists."""
    return sorted({ref for ref in REF.findall(text)
                   if ref not in KNOWN and ref not in FILE_REFS
                   and not _is_module(ref)})


def _asset_files():
    return (sorted((ASSETS / "commands").glob("*.md"))
            + sorted((ASSETS / "skills").rglob("*.md"))
            + sorted((ASSETS / "hooks").glob("*.py")))


def test_no_phantom_tool_references():
    phantoms = {}
    for path in _asset_files():
        bad = _phantom_refs(path.read_text("utf-8"))
        if bad:
            phantoms[path.name] = bad
    assert not phantoms, f"assets cite tools that do not exist: {phantoms}"


def test_a_real_module_is_not_a_phantom_but_an_invented_name_still_is():
    """The guard accepts a module that exists — the T2b imports
    `conscio.installer.durable` in the hook — and still refuses a name that
    exists neither as a tool nor as a module."""
    assert _phantom_refs("from conscio.installer.durable import resolve_space") == []
    assert _phantom_refs("run conscio.no_such_module_xyz now") == [
        "conscio.no_such_module_xyz"]


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
               - {ref for ref in cited
                  if ref in FILE_REFS or _is_module(ref)})
    assert not outside, f"SKILL.md cites tools absent from the plugin's default mode: {sorted(outside)}"
