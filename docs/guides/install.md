# Install

## From PyPI

```bash
pip install conscio
```

That is the whole runtime. Conscio's core depends only on `numpy` and the Python
standard library (`sqlite3`, `urllib`, `importlib.metadata`).

Requires Python ≥ 3.10.

## `conscio init` — bind a host (v2.11.0 "Reach")

One global install serves many agentic hosts (Claude Code, Antigravity, any MCP
host) on one machine. Run the interactive wizard once **per host**:

```bash
conscio init --host claude-code     # or: --host antigravity | --host other
```

It:

- creates a **per-host space** at `~/.conscio/instances/<slug>/` — its own
  identity (`instance.json`), `conscio.db`, sandbox, and `keys/` vault — so each
  editor is its own mind, while all of them share one **society**
  (`~/.hermes/{noosphere,liaison}.db`);
- emits the host's MCP launch config (`conscio-mcp --storage <space>` plus
  `env CONSCIO_VAULT_DIR=<space>/keys`), **backs up** the prior file
  (`.bak.<timestamp>`) and **reads it back** to confirm — it never fails silently;
- offers extras (**Graphify** structural cognition) and can start **Awake**;
- for `--host claude-code`, materializes a native bundle into `~/.claude/`: ten
  `/conscio:*` slash commands, a `conscio` skill, and a defensive SessionStart
  awareness hook.

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
