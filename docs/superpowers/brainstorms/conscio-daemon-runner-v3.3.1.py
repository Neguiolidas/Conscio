#!/usr/bin/env python3
"""Conscio daemon runner with per-cycle logging and Telegram notifications."""
import sys, time, logging, json, os, subprocess
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s %(name)s %(message)s')

sys.path.insert(0, '/home/ubuntu/clawd/Repos/Conscio')
from conscio.daemon import Daemon, _build_sensors, _load_config, _build_adapter_from_config
from conscio.engine import ConsciousnessEngine
from conscio.agency.loop import ActBudget, ActStatus
from conscio.workspace import WorkspaceContext
from pathlib import Path

cfg = _load_config()
model = cfg.get('model', 'mimo-v2.5-pro')
storage = str(Path.home() / '.hermes' / 'consciousness')

# ── Telegram notification helper ──────────────────────────────────────────
def notify_telegram(message: str, *, force: bool = False) -> None:
    """Send notification via hermes CLI (uses configured Telegram credentials)."""
    try:
        # Only notify on significant events, not every cycle
        subprocess.run(
            ['hermes', 'send', '--to', 'telegram', message],
            capture_output=True, timeout=10
        )
    except Exception as e:
        logging.debug('telegram notify failed: %s', e)

# Track last notification to avoid spam
_last_goal_notify = 0
_last_error_notify = 0
NOTIFY_COOLDOWN = 300  # 5 min between same type of notification

def on_cycle_hook(frames, result):
    """Hook called after each daemon cycle — sends Telegram notifications."""
    global _last_goal_notify, _last_error_notify
    now = time.time()

    # Notify on new goals
    if hasattr(result, 'reports') and result.reports:
        for report in result.reports:
            if hasattr(report, 'proposal') and report.proposal:
                goal_text = getattr(report.proposal, 'goal_text', '')
                if goal_text and (now - _last_goal_notify > NOTIFY_COOLDOWN):
                    tool = getattr(report.proposal, 'tool', '?')
                    notify_telegram(f'🧠 Conscio: {goal_text}\n🔧 Tool: {tool}')
                    _last_goal_notify = now

    # Notify on errors
    if hasattr(result, 'stopped') and result.stopped == 'lockdown':
        if now - _last_error_notify > NOTIFY_COOLDOWN:
            notify_telegram('⚠️ Conscio: LOCKDOWN — circuit breaker triggered')
            _last_error_notify = now

# ── Startup recovery (v3.3.1: no destructive cleanup, only quarantine) ──
import sqlite3
try:
    _db = sqlite3.connect(str(Path(storage) / 'conscio.db'))
    # Only clean error state, NEVER delete events/goals/actions
    _db.execute('DELETE FROM meta_errors')
    _db.execute('DELETE FROM trust_probation')
    _db.execute('DELETE FROM goal_quarantine')
    _db.commit()
    _db.close()
    logging.info('cleaned meta_errors, quarantine, trust_probation on startup')
except Exception as e:
    logging.warning('startup cleanup failed: %s', e)
# Clear action_lockdown without touching events/goals
try:
    _state_path = Path(storage) / 'state_summary.json'
    if _state_path.exists():
        _state = json.loads(_state_path.read_text())
        _state['action_lockdown'] = False
        _state_path.write_text(json.dumps(_state, indent=2))
    logging.info('cleared action_lockdown')
except Exception as e:
    logging.warning('lockdown cleanup failed: %s', e)

# ── Engine setup ─────────────────────────────────────────────────────────
engine = ConsciousnessEngine(model, storage_path=storage)
adapter = None
adapter_attached = False
adapter, atype = _build_adapter_from_config(cfg, fallback_model=model)
if adapter:
    try:
        pipeline = engine.attach_adapter(adapter, sandbox_root='/')
        adapter_attached = True
        logging.info('adapter attached OK: %s, pipeline=%s', atype, type(pipeline).__name__)
    except Exception as e:
        logging.exception('attach_adapter FAILED: %s (will run reflect-only)', e)
        adapter = None
if not adapter:
    logging.warning('no adapter available — reflect-only cycle (no act)')

engine.wake()
logging.info('engine.awake=%s (adapter=%s)', engine.awake, adapter_attached)

sensors = _build_sensors('host', agent_source=None)
# v3.3.1: raise failure threshold — NVIDIA proxy can timeout/slow
budget = ActBudget(max_cycles=4, max_failure_rate=0.8, min_attempts=6)
workspace = WorkspaceContext(emit=engine.event_bus.emit)
daemon = Daemon(engine, sensors=sensors, interval=5.0, budget=budget,
                workspace=workspace, on_cycle=on_cycle_hook)

notify_telegram('🚀 Conscio daemon started (awake mode ON)')
logging.info('daemon starting loop (interval=5s, budget_cycles=3)')
cycle = 0
while True:
    cycle += 1
    t0 = time.monotonic()
    try:
        result = daemon.cycle()
        elapsed = time.monotonic() - t0
        logging.info('cycle %d: %.1fs stopped=%s cycles=%d llm_calls=%d',
                     cycle, elapsed, result.stopped, result.cycles, result.llm_calls)
        hb = {'ts': time.time(), 'cycles': cycle, 'awake': True, 'pid': os.getpid()}
        Path(storage, 'daemon_heartbeat.json').write_text(json.dumps(hb, indent=2))
    except Exception as e:
        logging.exception('cycle %d failed: %s', cycle, e)
    time.sleep(5)
