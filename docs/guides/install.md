# Install

## As a Claude Code plugin

The shortest path for Claude Code users — memory, capture hooks and 14
`/conscio:*` slash commands, with no Python toolchain to manage:

```
/plugin marketplace add Neguiolidas/Conscio
/plugin install conscio
```

The plugin pins its MCP server to an exact version (`conscio==4.9.2`) and starts
it on the `balanced` tool surface. A version floor would let an old server serve
a tool list the shipped commands never mention, which reads as a bug in Claude
rather than as a stale install.

## Upgrading from 4.9.1 or earlier

Installs up to v4.9.1 materialized the Claude Code integration: hooks, commands
and skills copied into `~/.claude`, hook groups registered in
`~/.claude/settings.json`, and an MCP entry in `~/.claude.json` whose
`--storage` pointed at the legacy space (`~/.conscio/instances/<label>/`).
With the plugin installed those copies run twice (two Stop hooks, two
`conscio` MCP servers) and stale code. To upgrade:

```bash
pip install -U conscio          # or: update the plugin to the new version
```

1. Install the plugin as shown above (or let the wizard print the two
   `/plugin` commands: `conscio init --host claude-code`).
2. Start Claude Code once. The resolver adopts your legacy space — including
   a custom-label one (state B7) — instead of minting a blank identity, and
   writes the plugin pointer so the adoption survives.
3. If two live copies of a space conflict (state B3), resolve with
   `conscio space migrate --keep plugin` or `conscio space migrate
   --keep durable` — the copy not kept is renamed to a
   `.superseded-<UTC>` sibling, never deleted; the undo command is printed.
   The rename is liveness-gated on the copy being archived (with
   `--keep plugin`, the durable copy too — an active process or a recent
   write on that path defers it, exit 2, nothing touched).
4. Retire the old copies: `conscio init --host claude-code
   --remove-legacy`. It moves everything into
   `~/.conscio/legacy-backup/<UTC>/` and prunes only the Conscio hook
   entries in `settings.json` (your own hooks survive; a group or event is
   dropped only when it ends empty) and the `mcpServers.conscio` entry in
   `~/.claude.json` (rewritten with a `.bak.<ts>` backup only when it has
   that entry), deleting nothing. It parses both JSON files before moving
   anything — an unparseable one is exit 2 with nothing touched. It is
   identity-guarded on the space the old install was bound to (other
   runtimes' live spaces never block it): if that space is live and the
   plugin resolves elsewhere it refuses and says what to do — start Claude
   Code once, or pass `--retire-previous-space` to proceed with the space
   left on disk, no longer run. A second run prints `nothing to remove`.
5. Until you retire them, every session start prints one notice line naming
   the `--remove-legacy` command (it stats `~/.claude/hooks/` only — no JSON
   parsing; an unreadable dir is skipped, never raised — and it never
   blocks the session).

Everything below is for using Conscio as a library, a CLI, or from a host other
than Claude Code.

## From PyPI

```bash
pip install conscio
```

That is the whole runtime. Conscio's core depends on `numpy`, `cryptography` (Ed25519 signing of
noosphere artifacts) and the Python standard library (`sqlite3`, `urllib`, `importlib.metadata`).

Requires Python ≥ 3.10.

## `conscio init` — bind a host (v2.11.0 "Reach")

One global install serves many agentic hosts (Verdent, Antigravity,
any MCP host) on one machine. Run the interactive wizard once **per host**:

```bash
conscio init --host verdent     # or: antigravity | other
```

(For Claude Code, install the plugin via `/plugin` as shown above; `conscio init --host claude-code` will print the two `/plugin` commands and exit without writing. Coming from 4.9.1 or earlier? See [Upgrading](#upgrading-from-491-or-earlier).)

It:

- creates a **per-host space** at `~/.conscio/instances/<slug>/` — its own
  identity (`instance.json`), `conscio.db`, sandbox, and `keys/` vault — so each
  editor is its own mind, while all of them share one **society**
  (`~/.hermes/{noosphere,liaison}.db`);
- emits the host's MCP launch config (`conscio-mcp --storage <space>` plus
  `env CONSCIO_VAULT_DIR=<space>/keys`), **backs up** the prior file
  (`.bak.<timestamp>`) and **reads it back** to confirm — it never fails
  silently. The launcher is written as an **absolute path** whenever one can be
  proven, because a host started from a desktop icon does not inherit the
  virtualenv on your `PATH`; `--repair` re-resolves it after the venv moves;
- offers extras (**Graphify** structural cognition) and can start **Awake**.

`--host verdent` writes `~/.verdent/mcp.json` (press Enter to take it, or give a
project-level `<project>/.verdent/mcp.json` instead). Verdent has no plugin
installer: a server *is* its entry in that file, so this is the whole install.
See [MCP server](mcp.md#host-specific-placement) for the reconnect step an
**upgrade** needs — editing the file does not restart a server already running.

For a host whose MCP config path the wizard does not know (e.g. Antigravity),
give the path when asked or paste the printed JSON snippet into the host's MCP
config manually.

`conscio init --repair` revalidates/rewrites an existing host's binding.

### Per-host secret vault

Each space stores its API keys under `~/.conscio/instances/<slug>/keys/`,
selected by the `CONSCIO_VAULT_DIR` the installer writes into that host's launch
config. With no such env set, Conscio uses the legacy global vault
(`~/.config/conscio/keys/`) — existing single-host installs are unaffected.

> **Security note.** The per-host vault is *logical* least-privilege: each host
> is configured with only its own key. It is **not** OS-level isolation between
> processes of the same UNIX user — `0600`/`0700` permissions are per-user, so a
> compromised host running as the same user can still read other spaces' keys.
> True cross-host secret isolation needs an OS keyring or separate OS users.

## Console scripts

The wheel ships six commands:

| command | purpose |
|---|---|
| `conscio` | version / info / reflect / plugins / bench / consent / structure |
| `conscio-bench` | measure an inference backend against the agency pipeline |
| `conscio-daemon` | run Conscio as a living process (sensors → reflect → act) |
| `conscio-mcp` | stdlib-only MCP stdio server — embed Conscio in any MCP host (v2.0) |
| `conscio-hub` | localhost config/control plane — swap model/provider, toggle daemon awake (v2.1/v2.8) |
| `conscio-observatory` | read-only loopback viewer of one instance's persisted state (v2.4) |

```bash
conscio version
conscio --help
```

## Optional extras

Conscio keeps the core dependency-free; tooling lives in extras:

```bash
pip install "conscio[dev]"    # pytest, ruff, mypy — for contributing
pip install "conscio[docs]"   # mkdocs-material — to build this site
```

These never enter the runtime import graph.

Two optional runtime packages speed up semantic recall, and are auto-detected at
startup rather than configured (see [Migration](../MIGRATION.md)):

```bash
pip install hnswlib      # 2.9ms/search, 62× faster than numpy
pip install sqlite-vec   # 17ms/search, no extra RAM
```

Without either, Conscio falls back to the zero-dependency numpy backend.

## From source

```bash
git clone https://github.com/Neguiolidas/Conscio
cd Conscio
pip install -e ".[dev]"
```

## Local inference backends (optional)

`reflect()` needs no model. `act()` needs an inference backend — by default a
**local** one (Ollama, llama.cpp, or any OpenAI-compatible server such as LM
Studio, on `localhost`). See [Plugins](plugins.md) to wire your own.
