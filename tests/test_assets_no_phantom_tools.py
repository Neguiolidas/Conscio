"""Every conscio.<name> an asset mentions must be a tool the server can serve."""
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
# Python se importa. Citar um módulo numa docstring é legítimo, então cada um
# entra aqui à mão. Um módulo novo citado num asset QUEBRA o teste de propósito:
# obriga um humano a decidir a qual dos dois namespaces aquilo pertence. Não
# use find_spec() para automatizar isto — falharia aberto, engolindo uma tool
# fantasma sempre que o nome inventado coincidisse com um módulo real
# (`conscio.engine`, `conscio.mcp`…).
MODULE_REFS = {"conscio.timeutil"}


def _asset_files():
    return (sorted((ASSETS / "commands").glob("*.md"))
            + sorted((ASSETS / "skills").rglob("*.md"))
            + sorted((ASSETS / "hooks").glob("*.py")))


def test_no_phantom_tool_references():
    phantoms = {}
    for path in _asset_files():
        bad = {ref for ref in REF.findall(path.read_text("utf-8"))
               if ref not in KNOWN and ref not in MODULE_REFS}
        if bad:
            phantoms[path.name] = sorted(bad)
    assert not phantoms, f"assets cite tools that do not exist: {phantoms}"


def test_stack_dependent_commands_explain_instead_of_failing():
    for name in ("awake", "sleep", "society"):
        text = (ASSETS / "commands" / f"{name}.md").read_text("utf-8")
        assert "command -v conscio" in text, f"{name}.md has no binary guard"
        assert "pipx install conscio" in text, f"{name}.md does not say how to fix it"

    relay = (ASSETS / "commands" / "relay.md").read_text("utf-8")
    assert "conscio.relay_send" in relay
    assert "pipx install conscio" in relay


def test_skill_only_cites_tools_available_in_balanced():
    from conscio.mcp import modes
    text = (ASSETS / "skills" / "conscio" / "SKILL.md").read_text("utf-8")
    cited = set(REF.findall(text))
    outside = cited - modes.BALANCED_TOOLS - {MODE_TOOL_DEF["name"]} - MODULE_REFS
    assert not outside, f"SKILL.md cites tools absent from the plugin's default mode: {sorted(outside)}"
