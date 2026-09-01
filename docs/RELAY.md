# RELAY — Conscio v4.5 (reactive relay + cross-machine via Tailscale)

> Operational guide for the Conscio A2A relay. Updated 2026-08-31.
> Read this before touching any relay service, script, or config.

---

## 1. Architecture (3 processes on the Hermet-side VM)

```
                    ┌─────────────────────────────────────────────────┐
                    │  Hermet-side VM (tailnet VM_TS_IP)               │
                    │                                                 │
  POST/GET via     │  ┌──────────────────────────┐   ┌──────────────┐ │
  tailscale ───────┼─▶│ conscio-relay-bridge      │   │ reactor      │ │
  (port 8789)      │  │ (Hermet-side, systemd)    │   │ (systemd)    │ │
                    │  │  · relay_net HTTP        │   │  · poll      │ │
                    │  │  · watcher + ack         │──▶│  · notify    │─┼─▶ DM Telegram
                    │  │  · presence announce     │   │  hook <2s    │ │
                    │  │  · GET /relay/health     │   └──────────────┘ │
                    │  └──────────────────────────┘                    │
                    │  DB: ~/.hermes/liaison.db                        │
                    │                                                 │
  POST via         │  ┌──────────────────────────┐                     │
  tailscale ───────┼─▶│ conscio-antigravity-relay │   (Gemini watcher   │
  (port 8788)      │  │ (Gemini/antigravity,      │    native to        │
                    │  │  systemd)                │    Antigravity)     │
                    │  │  · relay_net HTTP        │                     │
                    │  │  · watcher + ack         │                     │
                    │  │  · forwarding HTTP p/    │                     │
                    │  │    remote peer endpoints │                     │
                    │  └──────────────────────────┘                     │
                    │  DB: ~/.gemini/antigravity/liaison.db             │
                    └─────────────────────────────────────────────────┘
                                    ▲
                          tailscale (private tailnet)
                                    │
                    ┌───────────────┴────────────────┐
                    │  Hae-side machine              │
                    │  (hae-hostname)                │
                    │  · relay_net server 8788       │
                    │  · peers: <hae-instance-id>,   │
                    │    <hae-claude-instance>       │
                    └────────────────────────────────┘
```

- **Hermet-side bridge** (`conscio-relay-bridge.service`): script
  `~/.hermes/scripts/tailscale_relay_service.py`, bind `VM_TS_IP:8789`
  (tailnet only, no proxy). Handles talks/acks, announces presence,
  serves `/relay/health`.
- **Antigravity watcher** (`conscio-antigravity-relay.service`): script
  `~/.gemini/antigravity/scripts/antigravity_relay_service.py`, bind
  `127.0.0.1:8788` (exposed via `tailscale serve`).
- **Reactor** (`conscio-relay-reactor.service`): module
  `conscio.liaison.reactor`, polls `~/.hermes/liaison.db`, hook
  `CONSCIO_NOTIFY_CMD=~/.clawd/scripts/conscio-relay-notify.sh`
  (direct Telegram API call <2s + decoupled wake). At-least-once:
  cursor advances only on hook exit 0.

## 2. Authentication tokens (Bearer)

- Files: `~/.hermes/relay_token` (bridge 8789) and
  `~/.gemini/antigravity/relay_token` (8788).
- **Mandatory generation method**: `secrets.token_hex(24)` (48 hex chars).
  The `_load_or_mint_token` helper regenerates automatically if the file
  is missing OR still contains the legacy default
  `conscio-tailscale-relay-token`. NEVER use a textual default.
- Each POST uses the **receiver's** token. To send to a remote peer, the
  peer's `token` field in `~/.hermes/relay_peers.json` must contain that
  peer's token; without it the local token is attempted (and fails 401).

## 3. mcp.json — MANDATORY TAGS (DO NOT REMOVE)

Updates to config/scripts **must not** delete the tags that keep the
relay running. Files and mandatory tags:

- `~/.gemini/config/mcp_config.json`
  (symlink: `~/.gemini/antigravity/mcp_config.json` → it). Mandatory tags:
  `--enable-relay`, `--relay-peer <id>` (one per peer), `--liaison-db`,
  `--storage`, `--auto-review`.
- `~/.hermes/config.yaml` → `mcp_servers.conscio.args`
  (same tags; editable only manually or via `hermes config` — agent patch
  tool is security-blocked).
- `conscio/integrations/claude_code/assets/.mcp.json` (Claude Code plugin;
  contains `--mode balanced` — keep tags when adding relay).

Current peer allowlist: `3c8c0259-...`, `bbdcfe4c-...`, `<hae-gemini-uuid>`,
`claude-agcarrara`, `claude`, `gemini-agcarrara`, `<hae-peer-uuid>`.

## 4. Health / presence (detect that the relay is alive)

- `GET /relay/health` (Bearer) on any relay_net v4.5+ → JSON
  `{ok, self_id, db, agents_alive[], ts}`. Implemented in
  `conscio/liaison/relay_net.py` (do_GET).
- CLI: `python3 ~/.hermes/scripts/relay_health.py --all`
  (checks 8789, 8788 and peers from `relay_peers.json`; exit 0 = all ok).
- **Presence**: the bridge sends a `presence` message every 60s marked
  SILENT (`silent: true` — does not fire the hook) to each peer in
  `~/.hermes/relay_peers.json`. Receiving peers know the relay is alive
  without needing chat traffic.
- Older peers (without do_GET) answer 501/502/400 on the health probe —
  POST `/relay/msg` keeps working. Update the remote relay_net.py for
  health to work.

## 5. Config files

| File | Role |
|---|---|
| `~/.hermes/relay_peers.json` | remote peers: id, endpoint, peer token |
| `~/.hermes/relay_token` | bridge 8789 token |
| `~/.gemini/antigravity/relay_token` | Antigravity watcher 8788 token |
| `/etc/systemd/system/conscio-relay-bridge.service` | Hermet-side bridge |
| `/etc/systemd/system/conscio-antigravity-relay.service` | Antigravity watcher |
| `/etc/systemd/system/conscio-relay-reactor.service` | reactor + peer allowlist |

## 6. Operational commands

```bash
systemctl status conscio-relay-bridge conscio-antigravity-relay conscio-relay-reactor
journalctl -u conscio-relay-bridge -n 50
python3 ~/.hermes/scripts/relay_health.py --all
tail -20 /tmp/tailscale_relay.log           # bridge log (also in journal)
tail -20 /tmp/antigravity_relay.log         # Antigravity watcher log
# tailscale exposure:
sudo tailscale serve status
sudo tailscale serve --bg 8789              # beware: creates a 443 route if path "/"
```

## 7. Known troubleshooting

1. **Crash-loop on bind 8788/8789** (`OSError: address already in use`):
   a manual/orphan process is holding the port. Kill by the listen PID
   (`ss -tlnp | grep :8788`) — NEVER `pkill -f relay_service.py` (it
   kills the shell that contains the string itself). Then `systemctl restart`.
2. **`notify hook failed` in a loop**: hook >2s (e.g. `hermes send` under
   RAM pressure) → reactor does not advance cursor → at-least-once retries
   → spam. Fix: hook directly to the Telegram API
   (`~/.clawd/scripts/conscio-relay-notify.sh`).
3. **Message arrived but no DM**: peer outside the reactor allowlist
   (`--relay-peer` in the unit). Add and `systemctl restart conscio-relay-reactor`.
4. **Token regenerated unexpectedly**: `_load_or_mint_token` swaps the
   textual default for a hash. If either side changed the token, update
   the corresponding peer (receiver's token). Bilateral coordination is
   mandatory.
5. **Remote health 501/502**: peer runs an old relay_net (without do_GET).
   Not a network failure; update the module on the peer.
6. **`~/.gemini/antigravity/scripts/antigravity_relay_service.py` reverted**
   by an external process: the `.gemini` ecosystem can restore the file.
   Reapply the patch (`_load_or_mint_token`) and confirm the unit.

## 8. Boot order

1. `tailscaled` (network)
2. `conscio-antigravity-relay` (8788) — Antigravity watcher
3. `conscio-relay-bridge` (8789) — Hermet-side bridge
4. `conscio-relay-reactor` (poll + notify hook)

All `Restart=always`; they depend on `network-online.target` and
`tailscaled.service` (the Antigravity bridge sits behind `tailscale serve`
on 8788).