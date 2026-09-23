"""v4.8.0 / v4.7.x: Derivation of host identity from process environment.

Precedence hierarchy:
  CLI flag > CONSCIO_IDENTITY_* env > Host derivation > "" (empty)

The server derives its identity purely from environment variables of the running
process (inside the host runtime). If no host signals are detected, returns an
empty identity with source="none" (honoring the contract: absence is not a prior,
never invent values).

Golden Rules (Hostile Review #271):
- Detection reads ONLY presence of environment keys, NEVER generic values like
  DEFAULT_MODEL or MODEL_NAME (avoids secret leakage and shell contamination).
- Model is never derived from generic env; only explicit flag/env set model.
- Familia is derived ONLY from a known model name. Without a known model,
  familia remains "" (never forced).
"""
import pytest

from conscio.mcp.host_identity import (
    HostIdentity,
    derive_familia_from_model,
    derive_host_identity,
)
from conscio.mcp.server import resolve_full_identity


class TestHostIdentityDerivation:
    def test_no_signals_yields_empty_with_source_none(self):
        # Empty env dict passed explicitly
        ident = derive_host_identity(env={})
        assert ident == HostIdentity(
            model="",
            familia="",
            runtime="",
            papel="",
            source="none",
        )
        assert ident.is_empty()

    def test_zcode_host_detection_primary_signals(self):
        # Primary signal from Relay #269 / #788: ZCODE_PLUGIN_DATA or ZCODE_PLUGIN_ID
        env = {
            "ZCODE_APP_VERSION": "3.14.3",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
            "ZCODE_PLUGIN_DATA": "${HOME}/.zcode/cli/plugins/data/conscio@conscio",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "zcode"
        assert ident.source == "zcode"
        assert ident.papel == "executor"
        assert ident.familia == ""
        assert ident.model == ""

    def test_zcode_does_not_read_generic_default_model(self):
        # Critical rule: generic DEFAULT_MODEL in env is NEVER read to avoid contamination
        env = {
            "ZCODE_ENV": "production",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
            "DEFAULT_MODEL": "agnes-3.0-flash",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "zcode"
        assert ident.model == ""
        assert ident.familia == ""
        assert ident.papel == "executor"
        assert ident.source == "zcode"

    def test_antigravity_host_detection_without_forced_familia(self):
        # Medium finding: without a model, familia remains empty (never forced to "gemini")
        env = {
            "CHROME_DEVTOOLS_MCP_JS": "/path/to/chrome-devtools-mcp.js",
            "AGY_BROWSER_ACTIVE_PORT_FILE": "/path/to/port",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "antigravity"
        assert ident.model == ""
        assert ident.familia == ""
        assert ident.papel == "executor"
        assert ident.source == "antigravity"

    def test_antigravity_does_not_read_generic_model_name(self):
        env = {
            "ANTIGRAVITY_AGENT": "1",
            "MODEL_NAME": "gemini-3.8-flash",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "antigravity"
        assert ident.model == ""
        assert ident.familia == ""
        assert ident.papel == "executor"

    def test_claude_code_host_detection_without_forced_familia(self):
        # Native Claude Code signals
        env = {
            "CLAUDE_PLUGIN_ROOT": "${HOME}/.claude/plugins/cache/conscio",
            "CLAUDECODE": "1",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "claude-code"
        assert ident.model == ""
        assert ident.familia == ""
        assert ident.papel == "executor"
        assert ident.source == "claude"

    def test_zcode_claude_plugin_prefers_zcode(self):
        # In ZCode running conscio plugin, both ZCODE_* and CLAUDE_PLUGIN_* exist
        env = {
            "ZCODE_APP_VERSION": "3.14.3",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
            "CLAUDE_PLUGIN_DATA": "${HOME}/.zcode/cli/plugins/data/conscio@conscio",
            "CLAUDE_PLUGIN_ROOT": "${HOME}/.zcode/cli/plugins/cache/conscio/conscio/4.7.0",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "zcode"
        assert ident.source == "zcode"

    def test_hermes_host_detection(self):
        env = {
            "HERMES_SESSION_ID": "20260905_192135_d405c347",
            "HERMES_HOME": "${HOME}/.hermes",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "hermes"
        assert ident.model == ""
        assert ident.familia == ""
        assert ident.papel == "executor"
        assert ident.source == "hermes"

    def test_opencode_host_detection(self):
        env = {
            "OPENCODE_CONFIG_DIR": "${HOME}/.config/opencode",
        }
        ident = derive_host_identity(env=env)
        assert ident.runtime == "opencode"
        assert ident.model == ""
        assert ident.familia == ""
        assert ident.papel == "executor"
        assert ident.source == "opencode"


class TestFamiliaMapping:
    @pytest.mark.parametrize(
        "model,expected_familia",
        [
            ("claude-opus-5", "claude"),
            ("claude-3-5-sonnet", "claude"),
            ("gemini-3.8-flash", "gemini"),
            ("gemini-2.5-pro", "gemini"),
            ("agnes-3.0-flash", "agnes"),
            ("agnes-mini", "agnes"),
            ("GLM-4-Plus", "glm"),
            ("glm-4", "glm"),
            ("deepseek-v4", "deepseek"),
            ("deepseek-chat", "deepseek"),
            ("gpt-4o", "openai"),
            ("qwen-2.5-72b", "qwen"),
            ("unknown-model-xyz", ""),
            ("", ""),
        ],
    )
    def test_derive_familia_from_model(self, model, expected_familia):
        assert derive_familia_from_model(model) == expected_familia


class TestPrecedenceHierarchy:
    """Precedence: flag > env > host derive > empty."""

    def test_flag_beats_everything(self):
        env = {
            "CONSCIO_IDENTITY_MODEL": "env-model",
            "ZCODE_APP_VERSION": "3.14.3",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
        }
        # If flag is supplied, it wins over env and derivation
        res = resolve_full_identity(
            model="flag-model",
            familia="flag-familia",
            runtime="flag-runtime",
            papel="flag-papel",
            env=env,
        )
        assert res == ("flag-model", "flag-familia", "flag-runtime", "flag-papel")

    def test_env_beats_derivation(self):
        env = {
            "CONSCIO_IDENTITY_RUNTIME": "custom-runtime",
            "ZCODE_APP_VERSION": "3.14.3",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
        }
        # Empty flag, but CONSCIO_IDENTITY_RUNTIME is present
        res = resolve_full_identity(
            model="",
            familia="",
            runtime="",
            papel="",
            env=env,
        )
        assert res[2] == "custom-runtime"

    def test_explicit_model_derives_familia_when_familia_absent(self):
        env = {
            "CONSCIO_IDENTITY_MODEL": "claude-opus-5",
            "ZCODE_APP_VERSION": "3.14.3",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
        }
        res = resolve_full_identity(
            model="",
            familia="",
            runtime="",
            papel="",
            env=env,
        )
        # model came from CONSCIO_IDENTITY_MODEL
        assert res[0] == "claude-opus-5"
        # familia derived from model
        assert res[1] == "claude"
        # runtime derived from ZCode
        assert res[2] == "zcode"
        # papel asserted as executor for detected host
        assert res[3] == "executor"

    def test_derivation_fills_only_empty_fields(self):
        env = {
            "CONSCIO_IDENTITY_PAPEL": "architect",
            "ZCODE_APP_VERSION": "3.14.3",
            "ZCODE_PLUGIN_ID": "conscio@conscio",
        }
        res = resolve_full_identity(
            model="gemini-3.8-flash",
            familia="",
            runtime="",
            papel="",
            env=env,
        )
        # model came from flag
        assert res[0] == "gemini-3.8-flash"
        # familia derived from model
        assert res[1] == "gemini"
        # runtime derived from ZCode
        assert res[2] == "zcode"
        # papel came from CONSCIO_IDENTITY_PAPEL
        assert res[3] == "architect"

    def test_empty_when_no_signals(self):
        res = resolve_full_identity(
            model="",
            familia="",
            runtime="",
            papel="",
            env={},
        )
        assert res == ("", "", "", "")


class TestMutantProtection:
    """Mutant tests with teeth: ensure absence never invents values."""

    def test_absence_never_invents_identity(self):
        env = {
            "USER": "ubuntu",
            "SHELL": "/bin/bash",
            "HOME": "${HOME}",
        }
        ident = derive_host_identity(env=env)
        assert ident.source == "none"
        assert ident.runtime == ""
        assert ident.familia == ""
        assert ident.model == ""
        assert ident.papel == ""
