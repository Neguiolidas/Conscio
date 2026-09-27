"""v4.8.0 / v4.7.x — Derivation of host identity from process environment.

When neither CLI flags (--identity-*) nor CONSCIO_IDENTITY_* env vars are set,
the server derives its identity purely from environment variables of the running
process (inside the host runtime).

Precedence hierarchy:
  CLI flag > CONSCIO_IDENTITY_* env > Host derivation > "" (empty)

Golden Rules:
1. Detection reads only PRESENCE of environment variable names, NEVER values
   (to prevent accidental leakage of secrets/tokens and avoid contamination
   from inherited shell environments).
2. Absence of signals = empty identity with source="none" (honoring the v4.7
   contract: absence is not a prior, never invent values).
3. Contamination is real: shell env can be inherited across processes. Only
   plugin/runtime-specific signals count (e.g. ZCODE_PLUGIN_*).
4. Model is NEVER derived from generic environment values (e.g. DEFAULT_MODEL,
   MODEL_NAME). Model comes exclusively from explicit CLI flag or
   CONSCIO_IDENTITY_MODEL env var.
5. Familia is derived ONLY from a known model name. Without a known model,
   familia remains "" (empty). No forced fallbacks (e.g. no 'or "gemini"').
6. Default papel is "executor" (asserted, not measured) ONLY when a host runtime
   is positively detected; when source="none", papel remains "".
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Sinais primários específicos do ZCode (exclusivos do contexto do plugin MCP)
# Ratificado no Relay #269: ZCODE_PLUGIN_DATA ou ZCODE_PLUGIN_ID
_ZCODE_PRIMARY_KEYS = ("ZCODE_PLUGIN_DATA", "ZCODE_PLUGIN_ID")
_ZCODE_FALLBACK_KEYS = ("ZCODE_APP_VERSION", "ZCODE_PROJECT_DIR")

# Sinais específicos do Antigravity (exclusivos da execução do MCP dentro do Antigravity)
_ANTIGRAVITY_PRIMARY_KEYS = (
    "CHROME_DEVTOOLS_MCP_JS",
    "AGY_BROWSER_ACTIVE_PORT_FILE",
    "AGY_BROWSER_WS_URL",
)
_ANTIGRAVITY_FALLBACK_KEYS = (
    "ANTIGRAVITY_AGENT",
    "ANTIGRAVITY_AGENTAPI_EXE",
)

# Sinais específicos do Claude Code nativo
# PROIBIDO: CLAUDE_PLUGIN_* isolado se ZCode estiver presente (ZCode injeta CLAUDE_PLUGIN_* por compatibilidade).
_CLAUDE_NATIVE_KEYS = ("CLAUDECODE", "CLAUDE_CODE")
_CLAUDE_PLUGIN_KEYS = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA")

# Sinais específicos do Hermes (runtime/gateway)
_HERMES_KEYS = ("HERMES_HOME", "HERMES_SESSION_ID", "HERMES_AGENT")

# Sinais específicos do OpenCode
_OPENCODE_KEYS = ("OPENCODE_CONFIG_DIR", "OPENCODE_SERVER", "OPENCODE_PROJECT")

# Mapeamento declarativo de famílias a partir do nome do modelo (lowercase substring match)
_FAMILIA_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    ("gemini", "gemini"),
    ("agnes", "agnes"),
    ("glm", "glm"),
    ("deepseek", "deepseek"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("qwen", "qwen"),
    ("llama", "meta"),
    ("mistral", "mistral"),
)


@dataclass(frozen=True)
class HostIdentity:
    """Identity derived from host process environment."""

    model: str = ""
    familia: str = ""
    runtime: str = ""
    papel: str = ""
    source: str = "none"

    def is_empty(self) -> bool:
        return not any((self.model, self.familia, self.runtime, self.papel))


def derive_familia_from_model(model: str) -> str:
    """Derive model family from model name using declarative prefix mapping."""
    m = (model or "").strip().lower()
    if not m:
        return ""
    for prefix, familia in _FAMILIA_PREFIXES:
        if prefix in m:
            return familia
    return ""


def derive_host_identity(env: Mapping[str, str] | None = None) -> HostIdentity:
    """Derive host identity strictly from environment variable PRESENCE.

    Evaluates process environment to identify the host runtime without
    reading sensitive values or generic model environment variables.

    Precedence (ZCode-ancestor fallback rule — fallback keys demoted to
    last resort):

    1. ZCode PRIMARY keys (``ZCODE_PLUGIN_DATA`` / ``ZCODE_PLUGIN_ID``) — the
       native ZCode injects them alongside the compat ``CLAUDE_PLUGIN_*``, so
       the native case decides here.
    2. Antigravity PRIMARY keys.
    3. Claude Code (``CLAUDECODE`` / ``CLAUDE_PLUGIN_*``) — wins EVEN when
       ``ZCODE_*`` are inherited from an ancestor ZCode session: the plugin
       that actually contains the storage decides (the earlier host-identity
       bug review — a claude-mcp launched
       from inside ZCode used to derive ``zcode``).
    4. Hermes keys.
    5. OpenCode keys.
    6. ZCode FALLBACK keys — only when NO other host signal is present.
    7. Antigravity FALLBACK keys — same rule.
    8. No signal → ``source="none"`` (honest empty identity).

    Rationale: FALLBACK keys are ambient signals inherited by ANY child of a
    ZCode session (measured in the earlier host-identity bug review — 12
    ``ZCODE_*`` vars in a claude process
    env), so they can never outrank a plugin-scoped signal.
    """
    if env is None:
        env = os.environ

    # 1. ZCode — only the PRIMARY (plugin-scoped) keys. Native ZCode injects
    #    ZCODE_PLUGIN_DATA == CLAUDE_PLUGIN_DATA, so the native case is decided
    #    here. (See the ZCode-ancestor fallback rule: the FALLBACK keys no
    #    longer decide up front — a claude/hermes launched FROM INSIDE ZCode
    #    inherits ZCODE_APP_VERSION and friends, and the deriver marked the
    #    host as zcode; measured in the earlier host-identity bug review.)
    if any(k in env for k in _ZCODE_PRIMARY_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="zcode",
            papel="executor",
            source="zcode",
        )

    # 2. Antigravity / Gemini — only the PRIMARY keys (same ZCode-ancestor
    #    fallback rule).
    if any(k in env for k in _ANTIGRAVITY_PRIMARY_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="antigravity",
            papel="executor",
            source="antigravity",
        )

    # 3. Claude Code nativo
    # Present if CLAUDECODE or CLAUDE_PLUGIN_* is set — even with ZCODE_*
    # inherited from an ancestor ZCode session: the plugin that actually
    # contains the storage decides (see the ZCode-ancestor fallback rule).
    if any(k in env for k in _CLAUDE_NATIVE_KEYS) or any(k in env for k in _CLAUDE_PLUGIN_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="claude-code",
            papel="executor",
            source="claude",
        )

    # 4. Hermes
    if any(k in env for k in _HERMES_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="hermes",
            papel="executor",
            source="hermes",
        )

    # 5. OpenCode
    if any(k in env for k in _OPENCODE_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="opencode",
            papel="executor",
            source="opencode",
        )

    # 6. ZCode FALLBACK (ZCode-ancestor fallback rule: last step before
    #    source=none — only decides when NO other host signal is present;
    #    inherited ancestor environment does not count).
    if any(k in env for k in _ZCODE_FALLBACK_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="zcode",
            papel="executor",
            source="zcode",
        )

    # 7. Antigravity FALLBACK (same ZCode-ancestor fallback rule).
    if any(k in env for k in _ANTIGRAVITY_FALLBACK_KEYS):
        return HostIdentity(
            model="",
            familia="",
            runtime="antigravity",
            papel="executor",
            source="antigravity",
        )

    # 8. No host signal identified: strict None/absence contract
    return HostIdentity(
        model="",
        familia="",
        runtime="",
        papel="",
        source="none",
    )
