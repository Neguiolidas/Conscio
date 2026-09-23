"""v4.7.x: CONSCIO_IDENTITY_* env vars — per-host identity without args override.

The Agnes/ZCode gap (relay #778): the shared plugin asset passes no
--identity-* flags, and ZCode does not allow per-host MCP args override. The
server's next boot republishes the card with explicit "" and wipes the rich
identity (v4.6.8 contract: explicit value writes). Env vars give every host
the per-host path without burning other hosts in the shared asset.

Precedence: CLI flag > env var > "" (today's default). A flag explicitly
passed still wins; env fills when the flag is absent.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _identity_from_env(monkeypatch, env):
    """Import server's resolver fresh with the env applied."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # import inside the test so monkeypatch applies before resolution
    sys.path.insert(0, str(REPO))
    from conscio.mcp import server
    return server


class TestIdentityEnvResolution:
    def test_env_model_used_when_flag_absent(self, monkeypatch):
        server = _identity_from_env(monkeypatch, {
            "CONSCIO_IDENTITY_MODEL": "agnes-3.0-flash",
            "CONSCIO_IDENTITY_FAMILIA": "agnes",
            "CONSCIO_IDENTITY_RUNTIME": "zcode",
            "CONSCIO_IDENTITY_PAPEL": "executor",
        })
        assert server.resolve_identity_env(
            model="", familia="", runtime="", papel=""
        ) == ("agnes-3.0-flash", "agnes", "zcode", "executor")

    def test_flag_beats_env(self, monkeypatch):
        server = _identity_from_env(monkeypatch, {
            "CONSCIO_IDENTITY_MODEL": "agnes-3.0-flash",
        })
        assert server.resolve_identity_env(
            model="claude-opus-5", familia="", runtime="", papel=""
        )[0] == "claude-opus-5"

    def test_defaults_when_nothing_set(self, monkeypatch):
        server = _identity_from_env(monkeypatch, {})
        assert server.resolve_identity_env(
            model="", familia="", runtime="", papel=""
        ) == ("", "", "", "")

    def test_partial_env_only_fills_absent(self, monkeypatch):
        server = _identity_from_env(monkeypatch, {
            "CONSCIO_IDENTITY_RUNTIME": "zcode",
        })
        model, familia, runtime, _papel = server.resolve_identity_env(
            model="agnes-3.0-flash", familia="", runtime="", papel=""
        )
        assert model == "agnes-3.0-flash"   # flag passes through
        assert runtime == "zcode"            # env fills the absent flag
        assert familia == ""                  # untouched
