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
raw API key — providers store an `api_key_env` (the NAME of an environment
variable), resolved to a value only when an adapter is built.

Pages: choose a provider, pick or type a model (auto-discovered where the
provider exposes a model listing), **Test** it (one live call), then **Save** —
the change applies on the next engine/daemon start.
