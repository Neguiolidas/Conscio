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
