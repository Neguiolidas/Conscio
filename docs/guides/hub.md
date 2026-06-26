# Conscio Hub (v2.1)

The Hub is a localhost control plane for swapping Conscio's active model and
provider without hand-editing `~/.config/conscio/config.json`.

```bash
conscio-hub                 # http://127.0.0.1:8787
conscio-hub --port 9000
CONSCIO_HUB_TOKEN=secret conscio-hub   # require Bearer auth on /api/*
```

It is **engine-free**: it edits the config that engines read on their next boot.
It never executes actions, never touches the cognition loop, and never returns a
raw API key — the config stores only an `api_key_env` (the NAME of an environment
variable), resolved to a value only when an adapter is built.

Pages: choose a provider, paste your **API Key**, set a **Base URL** if the
provider is custom, pick or type a model (auto-discovered where the provider
exposes a model listing), **Test** it (one live call), then **Save** — the change
applies on the next engine/daemon start.

### Key vault

When you save a raw key in the **API Key** field, the Hub writes it to a
per-provider file under `~/.config/conscio/keys/` — the directory is created
`0700` and each key file `0600` (created with those permissions, never world
-readable even briefly). The `config.json` keeps only the generated
`api_key_env` reference; `validate()` rejects a raw `api_key` in the config, and
the key is never echoed back over the API (a GET reports only
`api_key_present: true/false`). Filenames are derived from sanitised provider
inputs and validated against the env-var-name regex, so a hostile provider
`type` cannot escape the vault directory.

### Daemon control (opt-in, v2.8)

The Hub can also toggle a running daemon's **awake** state — without signals,
PIDs, or `os.kill`. It is a **double opt-in**, off by default:

```bash
conscio-hub --enable-daemon-control --storage ~/.hermes/consciousness
conscio-daemon --watch-control                    # the daemon honors the file
```

- With `--enable-daemon-control`, the Hub exposes `PUT /api/daemon/awake {awake}`
  and `GET /api/daemon/control`, and the UI shows an **Awake** toggle. The PUT
  writes `daemon_control.json` (atomically) into `--storage` (default
  `~/.hermes/consciousness`, matching the engine's default). Without the flag,
  both routes return **404** and no toggle renders.
- A daemon launched with `--watch-control` reads that file at the **top of each
  cycle** and applies it via `engine.wake()` / `engine.sleep()` — so a toggle
  takes effect on the next heartbeat, not the next boot. The daemon reads the
  file by name only; the Hub never signals the process.
- The control file is **authority** while watching: on restart the daemon
  applies the last toggled state, overriding the launch `--awake`.

> **Autonomy warning.** Flipping awake to **true** makes a daemon that has an
> adapter + act budget **autonomous**. The control file only toggles `awake`; it
> never attaches an adapter or enables act (both are launch-time). On a
> multi-user host, run the Hub with `--token` — anyone who can reach the loopback
> port (or write the storage dir) can flip the toggle.
