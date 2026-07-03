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
    "initiate": "initiate: proactively message peers while Awake (a daemon "
                "flag — applied when the Awake daemon is started).",
}
# every consent the wizard collects; hostcfg._FLAG_ARG decides which of these
# reach the conscio-mcp entry ("initiate" is daemon-only: conscio-daemon
# --initiate; conscio-mcp rejects it)
_CONSENT_KEYS = ("act", "hermes", "relay", "initiate")


class PromptIO(Protocol):
    def ask(self, prompt: str, default: str = "") -> str: ...
    def confirm(self, prompt: str, default: bool = False) -> bool: ...
    def echo(self, msg: str) -> None: ...


def _write_generic(io: PromptIO, slug: str, flags: dict, model, ts: str,
                   *, repair: bool = False) -> None:
    path = io.ask("Antigravity/MCP config path (blank = print snippet)",
                  default="")
    if path:
        p = Path(path)
        # repair: never strip flags/model already granted in the target config
        use = {**flags, **hostcfg.existing_flags(p)} if repair else flags
        if repair:
            model = model or hostcfg.existing_model(p)
        hostcfg.backup_then_write_json(
            p,
            mutate=lambda o: hostcfg.upsert_conscio_entry(
                o, slug, flags=use, model=model),
            verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)
        io.echo(f"wrote conscio MCP entry to {path}")
    else:
        io.echo(hostcfg.generic_snippet(slug, flags=flags, model=model))


def run_with(io: PromptIO, *, host: str, repair: bool, model: "str | None",
             ts: str, label: "str | None" = None) -> int:
    if repair and host == "claude-code":
        # repair rebinds the EXISTING space: recover slug + model from the
        # current entry so a rerun never mints a fresh empty mind or strips
        # the configured model (pre-Reach defaults produced doubled slugs)
        from ..integrations.claude_code.materialize import claude_json_path
        cfg = claude_json_path(None)
        label = label or hostcfg.existing_slug(cfg)
        model = model or hostcfg.existing_model(cfg)
    label = label or io.ask("Space label", default=host)
    slug = spaces.slugify(label)
    sp, ident, created = spaces.ensure_space(slug)
    io.echo(f"space {'created' if created else 'reused'}: {sp} "
            f"(instance {ident.instance_id[:12]})")

    flags: dict = {k: False for k in _CONSENT_KEYS}
    if repair:
        # repair rewrites the binding only — recover the flags the user had
        # already granted instead of silently downgrading them to OFF
        if host == "claude-code":
            flags.update(hostcfg.existing_flags(cfg))
        if flags.get("initiate"):
            io.echo("  note: initiate applies to the Awake daemon — start it "
                    "with `conscio daemon --awake --initiate`.")
    else:
        for key in _CONSENT_KEYS:
            io.echo(_FLAG_HELP[key])
            flags[key] = io.confirm(f"enable {key}?", default=False)

        # extras
        g = extras.REGISTRY["graphify"]
        if io.confirm(g.summary + "  enable Graphify?", default=False):
            for step in g.enable(sp):
                io.echo(f"  run: {step}")

        # awake
        if io.confirm("start Awake daemon now?", default=False):
            extra = ["--awake"] + (["--initiate"] if flags["initiate"] else [])
            try:
                pid = daemonctl.start(slug, extra_args=extra)
                io.echo(f"  awake daemon pid {pid}")
            except daemonctl.DaemonStartError as exc:
                io.echo(f"  awake daemon start FAILED: {exc}")
        elif flags["initiate"]:
            io.echo("  note: initiate applies to the Awake daemon — start it "
                    "later with `conscio daemon --awake --initiate`.")

    # emit host config
    if host == "claude-code":
        from ..integrations.claude_code.materialize import materialize
        materialize(slug, flags=flags, model=model, ts=ts, io=io)
    else:
        _write_generic(io, slug, flags, model, ts, repair=repair)
    return 0
