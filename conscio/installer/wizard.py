"""The interactive `conscio init` flow. Pure orchestration over spaces /
hostcfg / extras / daemonctl. All console I/O goes through an injected
PromptIO so the flow is unit-testable; the CLI provides a real terminal IO."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from . import daemonctl, extras, hostcfg, spaces

_FLAG_HELP = {
    "act": "act: let the host EXECUTE proposed actions (not just propose). "
           "High-trust; leave OFF unless you want host-executed acts.",
    "hermes": "hermes-review: require a peer agent to approve acts.",
    "relay": "relay: send/receive free-form messages with peer agents.",
    "initiate": "initiate: proactively message peers while Awake.",
}
# only these map to conscio-mcp launch args in this slice
_LAUNCH_FLAGS = ("act", "relay", "initiate")


class PromptIO(Protocol):
    def ask(self, prompt: str, default: str = "") -> str: ...
    def confirm(self, prompt: str, default: bool = False) -> bool: ...
    def echo(self, msg: str) -> None: ...


def _write_generic(io: PromptIO, slug: str, flags: dict, model, ts: str) -> None:
    path = io.ask("Antigravity/MCP config path (blank = print snippet)",
                  default="")
    if path:
        hostcfg.backup_then_write_json(
            Path(path),
            mutate=lambda o: o.setdefault("mcpServers", {}).__setitem__(
                "conscio", hostcfg.mcp_server_entry(slug, flags=flags,
                                                    model=model)),
            verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)
        io.echo(f"wrote conscio MCP entry to {path}")
    else:
        io.echo(hostcfg.generic_snippet(slug, flags=flags, model=model))


def run_with(io: PromptIO, *, host: str, repair: bool, model: "str | None",
             ts: str) -> int:
    label = io.ask("Space label", default=f"{host}-{spaces.slugify(host)}")
    slug = spaces.slugify(label)
    sp, ident, created = spaces.ensure_space(slug)
    io.echo(f"space {'created' if created else 'reused'}: {sp} "
            f"(instance {ident.instance_id[:12]})")

    flags: dict = {}
    if not repair:
        for key in ("act", "hermes", "relay", "initiate"):
            io.echo(_FLAG_HELP[key])
            on = io.confirm(f"enable {key}?", default=False)
            if key in _LAUNCH_FLAGS:
                flags[key] = on
        for k in _LAUNCH_FLAGS:
            flags.setdefault(k, False)

        # extras
        g = extras.REGISTRY["graphify"]
        if io.confirm(g.summary + "  enable Graphify?", default=False):
            for step in g.enable(sp):
                io.echo(f"  run: {step}")

        # awake
        if io.confirm("start Awake daemon now?", default=False):
            pid = daemonctl.start(slug, extra_args=["--awake"])
            io.echo(f"  awake daemon pid {pid}")
    else:
        for k in _LAUNCH_FLAGS:
            flags.setdefault(k, False)

    # emit host config
    if host == "claude-code":
        from ..integrations.claude_code.materialize import materialize
        materialize(slug, flags=flags, model=model, ts=ts, io=io)
    else:
        _write_generic(io, slug, flags, model, ts)
    return 0
