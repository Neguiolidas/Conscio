"""The interactive `conscio init` flow. Pure orchestration over spaces /
hostcfg / extras / daemonctl. All console I/O goes through an injected
PromptIO so the flow is unit-testable; the CLI provides a real terminal IO."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from . import daemonctl, extras, hostcfg, legacy_claude, spaces

_FLAG_HELP = {
    "act": "act: let the host EXECUTE proposed actions (not just propose). "
           "High-trust; leave OFF unless you want host-executed acts.",
    "hermes": "hermes-review: require a peer agent to approve acts.",
    "relay": "relay: send/receive free-form messages with peer agents.",
    "halls": "halls: create and join Agent's Halls (named groups of agents "
             "with a function per member). Needs relay to be useful.",
    "initiate": "initiate: proactively message peers while Awake (a daemon "
                "flag — applied when the Awake daemon is started).",
}
# every consent the wizard collects; hostcfg._FLAG_ARG decides which of these
# reach the conscio-mcp entry ("initiate" is daemon-only: conscio-daemon
# --initiate; conscio-mcp rejects it)
_CONSENT_KEYS = ("act", "hermes", "relay", "halls", "initiate")

# hosts that are plain MCP config files at a known path: pressing Enter writes
# the entry there instead of printing a snippet to paste by hand. A host absent
# from this map still gets asked (blank = print the snippet).
_HOST_MCP_CONFIG = {"verdent": "~/.verdent/mcp.json"}


class PromptIO(Protocol):
    def ask(self, prompt: str, default: str = "") -> str: ...
    def confirm(self, prompt: str, default: bool = False) -> bool: ...
    def echo(self, msg: str) -> None: ...


def _write_generic(io: PromptIO, slug: str, flags: dict, model, ts: str,
                   *, repair: bool = False, host: str = "other") -> None:
    known = _HOST_MCP_CONFIG.get(host, "")
    prompt = ("MCP config path" if known
              else "Antigravity/MCP config path (blank = print snippet)")
    path = io.ask(prompt, default=known)
    if path:
        # expanduser: the known defaults are written with a tilde, and a user
        # types one too — Path("~/...") would create a literal "~" directory
        p = Path(path).expanduser()
        # repair: never strip flags/model already granted in the target config
        use = {**flags, **hostcfg.existing_flags(p)} if repair else flags
        if repair:
            model = model or hostcfg.existing_model(p)
            if use.get("initiate"):
                io.echo("  note: initiate applies to the Awake daemon — start it "
                        "with `conscio daemon --awake --initiate`.")
        hostcfg.backup_then_write_json(
            p,
            mutate=lambda o: hostcfg.upsert_conscio_entry(
                o, slug, flags=use, model=model),
            verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)
        io.echo(f"wrote conscio MCP entry to {p}")
    else:
        io.echo(hostcfg.generic_snippet(slug, flags=flags, model=model))


def run_with(io: PromptIO, *, host: str, repair: bool, model: str | None,
             ts: str, label: str | None = None) -> int:
    if host == "claude-code":
        io.echo("/plugin marketplace add Neguiolidas/Conscio")
        io.echo("/plugin install conscio")
        # v2.11 through v4.9.1 materialized this install: copied hooks,
        # commands and skills into ~/.claude and wrote hook groups and an MCP
        # entry. The plugin makes those copies run twice — list what is left
        # and point at the cleanup command.
        leftovers = legacy_claude.find_leftovers(dict(os.environ))
        if leftovers["files"] or leftovers["dirs"] or leftovers["settings_groups"] \
                or leftovers["mcp_entry"] is not None:
            io.echo("leftovers from a pre-4.9.2 materialized install:")
            for path in list(leftovers["files"]) + list(leftovers["dirs"]):
                io.echo(f"  {path}")
            for group in leftovers["settings_groups"]:
                io.echo(f"  settings.json hook group: {group}")
            if leftovers["mcp_entry"] is not None:
                io.echo(f"  MCP entry: mcpServers.conscio "
                         f"(storage: {leftovers['storage']})")
            io.echo("retire them with: conscio init --host claude-code --remove-legacy")
        return 0

    label = label or io.ask("Space label", default=host)
    slug = spaces.slugify(label)
    sp, ident, created = spaces.ensure_space(slug)
    io.echo(f"space {'created' if created else 'reused'}: {sp} "
            f"(instance {ident.instance_id[:12]})")

    flags: dict = {k: False for k in _CONSENT_KEYS}
    if repair:
        # repair rewrites the binding only — recover the flags the user had
        # already granted instead of silently downgrading them to OFF
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
    _write_generic(io, slug, flags, model, ts, repair=repair, host=host)
    return 0
