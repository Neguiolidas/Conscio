#!/usr/bin/env python3
"""
Conscio Reflection Script — Run by cron every 30 minutes.

Reads world state from Hermes consciousness data,
runs a reflection cycle, and outputs the state summary.
This output gets injected into the next cron prompt as context.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add repo to path
sys.path.insert(0, "/home/ubuntu/clawd/Repos/Conscio")

from conscio import ConsciousnessEngine
from conscio.models import ModelRegistry, ContextMode
from conscio.event_bus import EventBus
from conscio.content_store import ContentStore
from conscio.token_tracker import TokenTracker

# Configuration
STORAGE_PATH = Path.home() / ".hermes" / "consciousness"
MODEL_NAME = os.environ.get("CONSCIO_MODEL", "glm-5.1")

TRADING_BOT_PORT = int(os.environ.get("TRADING_BOT_PORT", "8080"))
TRADING_BOT_HOST = os.environ.get("TRADING_BOT_HOST", "127.0.0.1")


def collect_system_state() -> str:
    """
    Active perception: gather real system metrics.
    CPU, memory, disk, load, uptime.
    """
    parts = []

    # Uptime
    try:
        uptime_raw = Path("/proc/uptime").read_text().split()[0]
        uptime_h = float(uptime_raw) / 3600
        parts.append(f"Uptime: {uptime_h:.1f}h")
    except Exception:
        pass

    # Load average
    try:
        load = os.getloadavg()
        parts.append(f"Load: {load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f}")
    except Exception:
        pass

    # Memory
    try:
        free_out = subprocess.run(
            ["free", "-m"], capture_output=True, text=True, timeout=5
        ).stdout
        lines = free_out.strip().split("\n")
        if len(lines) >= 2:
            vals = lines[1].split()
            total, used, avail = int(vals[1]), int(vals[2]), int(vals[6]) if len(vals) > 6 else 0
            pct = (used / total * 100) if total else 0
            parts.append(f"RAM: {used}/{total}MB ({pct:.0f}%), avail {avail}MB")
    except Exception:
        pass

    # Disk
    try:
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100
        parts.append(f"Disk: {usage.used/1e9:.1f}/{usage.total/1e9:.1f}GB ({pct:.0f}%)")
    except Exception:
        pass

    # CPU count
    try:
        parts.append(f"CPUs: {os.cpu_count()}")
    except Exception:
        pass

    return " | ".join(parts) if parts else "System: unavailable"


def collect_trading_bot_state() -> str:
    """
    Active perception: gather trading bot state via HTTP API.
    Falls back gracefully if bot is unreachable.
    """
    import urllib.request
    import urllib.error

    parts = []

    try:
        url = f"http://{TRADING_BOT_HOST}:{TRADING_BOT_PORT}/api/status"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        # Extract key fields
        pnl = data.get("daily_pnl", data.get("pnl", "N/A"))
        bankroll = data.get("bankroll", data.get("balance", "N/A"))
        positions = data.get("open_positions", [])
        if isinstance(positions, list):
            parts.append(f"Bot PnL: {pnl} | Bankroll: {bankroll} | Positions: {len(positions)}")
        else:
            parts.append(f"Bot PnL: {pnl} | Bankroll: {bankroll}")

        # Active signals if available
        signals = data.get("recent_signals", data.get("signals", []))
        if isinstance(signals, list) and signals:
            parts.append(f"Recent signals: {len(signals)}")

    except urllib.error.URLError:
        parts.append("Bot: offline")
    except Exception as e:
        parts.append(f"Bot: error ({type(e).__name__})")

    return " | ".join(parts) if parts else "Bot: unreachable"


def collect_network_state() -> str:
    """
    Active perception: basic network connectivity check.
    """
    parts = []

    # Check internet connectivity
    try:
        import socket
        sock = socket.create_connection(("1.1.1.1", 53), timeout=3)
        sock.close()
        parts.append("Internet: up")
    except Exception:
        parts.append("Internet: down")

    # Check if key services are listening
    try:
        result = subprocess.run(
            ["ss", "-tlnp"], capture_output=True, text=True, timeout=5
        )
        listening = []
        for line in result.stdout.strip().split("\n")[1:]:
            fields = line.split()
            if len(fields) >= 4:
                addr = fields[3]
                if "127.0.0.1" in addr or "::1" in addr:
                    listening.append(addr)
        if listening:
            parts.append(f"Local services: {len(listening)}")
    except Exception:
        pass

    return " | ".join(parts) if parts else "Network: unavailable"

def gather_world_state() -> str:
    """
    Gather current world state from various sources.
    This is the PERCEIVE step — what does the agent see right now?
    """
    parts = []
    
    # System time
    now = datetime.now()
    parts.append(f"Time: {now.strftime('%Y-%m-%d %H:%M')} BRT")
    
    # ACTIVE PERCEPTION: real system metrics
    sys_state = collect_system_state()
    if sys_state != "System: unavailable":
        parts.append(f"System: {sys_state}")
    
    # ACTIVE PERCEPTION: trading bot state
    bot_state = collect_trading_bot_state()
    if bot_state != "Bot: unreachable":
        parts.append(f"Trading: {bot_state}")
    
    # ACTIVE PERCEPTION: network state
    net_state = collect_network_state()
    if net_state != "Network: unavailable":
        parts.append(f"Network: {net_state}")
    
    # Load previous state to detect changes
    state_path = STORAGE_PATH / "state_summary.txt"
    if state_path.exists():
        prev = state_path.read_text().strip()
        if prev:
            parts.append(f"Previous state: {prev[:200]}")
    
    # Load world model summary
    wm_path = STORAGE_PATH / "world_model.json"
    if wm_path.exists():
        try:
            wm = json.loads(wm_path.read_text())
            entity_count = len(wm.get("entities", {}))
            relation_count = len(wm.get("relations", []))
            stale = []
            for name, info in wm.get("entities", {}).items():
                updated = info.get("last_updated", "")
                if updated:
                    try:
                        dt = datetime.fromisoformat(updated)
                        if (now - dt).total_seconds() > 86400:  # 24h
                            stale.append(name)
                    except:
                        pass
            parts.append(f"World model: {entity_count} entities, {relation_count} relations")
            if stale:
                parts.append(f"Stale entities: {', '.join(stale[:5])}")
        except json.JSONDecodeError:
            pass
    
    # Load goals summary
    goals_path = STORAGE_PATH / "goals.json"
    if goals_path.exists():
        try:
            goals = json.loads(goals_path.read_text())
            active = [g for g in goals if g.get("status") == "active"]
            if active:
                goal_descs = [g.get("description", "?")[:60] for g in active[:5]]
                parts.append(f"Active goals: {'; '.join(goal_descs)}")
        except json.JSONDecodeError:
            pass
    
    # Load meta-cognition summary
    meta_path = STORAGE_PATH / "meta_cognition.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            history = meta.get("confidence_history", [])
            if history:
                recent = history[-5:]
                avg_conf = sum(h.get("confidence", 0.5) for h in recent) / len(recent)
                parts.append(f"Recent confidence: {avg_conf:.0%}")
            blind = meta.get("blind_spots", [])
            if blind:
                parts.append(f"Blind spots: {', '.join(blind[:3])}")
            errors = meta.get("error_patterns", [])
            freq = [e for e in errors if e.get("count", 0) >= 2]
            if freq:
                parts.append(f"Recurring errors: {len(freq)} patterns")
        except json.JSONDecodeError:
            pass
    
    # Check pending evolution proposals
    evo_path = STORAGE_PATH / "evolution_proposals.json"
    if evo_path.exists():
        try:
            proposals = json.loads(evo_path.read_text())
            pending = [p for p in proposals if p.get("status") == "pending"]
            if pending:
                parts.append(f"⚠️ {len(pending)} evolution proposal(s) pending approval")
                for p in pending[:3]:
                    parts.append(f"  → {p.get('description', '?')[:80]}")
        except json.JSONDecodeError:
            pass
    
    return "\n".join(parts) if parts else "No world state available — first run."


def detect_anomalies() -> list[str]:
    """
    Detect anomalies from the world state.
    This drives the CURIOSITY goal generator.
    """
    anomalies = []
    
    # --- System-level anomaly detection ---
    
    # High load
    try:
        load = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        if load[0] > cpu_count * 2:
            anomalies.append(f"High system load: {load[0]:.2f} (CPUs: {cpu_count})")
    except Exception:
        pass
    
    # Low memory
    try:
        free_out = subprocess.run(
            ["free", "-m"], capture_output=True, text=True, timeout=5
        ).stdout
        lines = free_out.strip().split("\n")
        if len(lines) >= 2:
            vals = lines[1].split()
            total, avail = int(vals[1]), int(vals[6]) if len(vals) > 6 else 0
            if total > 0 and avail / total < 0.1:
                anomalies.append(f"Low memory: {avail}MB available of {total}MB")
    except Exception:
        pass
    
    # Low disk
    try:
        usage = shutil.disk_usage("/")
        if usage.total > 0 and usage.free / usage.total < 0.1:
            anomalies.append(f"Low disk: {usage.free/1e9:.1f}GB free of {usage.total/1e9:.1f}GB")
    except Exception:
        pass
    
    # --- Consciousness-level anomaly detection ---
    
    # Check for stale entities (might indicate something stopped updating)
    wm_path = STORAGE_PATH / "world_model.json"
    if wm_path.exists():
        try:
            wm = json.loads(wm_path.read_text())
            now = datetime.now()
            for name, info in wm.get("entities", {}).items():
                updated = info.get("last_updated", "")
                if updated:
                    try:
                        dt = datetime.fromisoformat(updated)
                        hours_stale = (now - dt).total_seconds() / 3600
                        if hours_stale > 48:
                            anomalies.append(f"Entity '{name}' hasn't updated in {hours_stale:.0f}h")
                    except:
                        pass
        except:
            pass
    
    # Check for pending evolution proposals (they need attention)
    evo_path = STORAGE_PATH / "evolution_proposals.json"
    if evo_path.exists():
        try:
            proposals = json.loads(evo_path.read_text())
            pending = [p for p in proposals if p.get("status") == "pending"]
            if len(pending) > 5:
                anomalies.append(f"{len(pending)} evolution proposals piling up without review")
        except:
            pass
    
    return anomalies


def main():
    # Ensure storage exists
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    
    # Initialize engine (includes v0.2 modules)
    engine = ConsciousnessEngine(
        model_name=MODEL_NAME,
        storage_path=STORAGE_PATH,
    )
    
    # --- v0.2: Direct EventBus access for perception events ---
    bus = engine.event_bus
    store = engine.content_store
    tracker = engine.token_tracker
    
    # PERCEIVE
    world_state = gather_world_state()
    anomalies = detect_anomalies()
    
    # Emit perception events (before reflection)
    bus.emit(type="perception", category="system",
             data={"world_state_length": len(world_state)})
    
    # Index raw world state for future search
    store.index(
        label=f"world_state_{datetime.now().strftime('%Y%m%d_%H%M')}",
        content=world_state,
        category="perception",
    )
    
    for anomaly in anomalies:
        bus.emit(type="anomaly", category="system",
                 data={"description": anomaly}, priority=8)
    
    # Estimate confidence based on recent history
    confidence = engine.meta.average_confidence()
    if confidence == 0.5:  # Default means no data yet
        confidence = 0.7  # Start optimistic
    
    # REFLECT
    result = engine.reflect(
        world_state=world_state,
        confidence=confidence,
        anomalies=anomalies,
    )
    
    # Get the compact state for injection
    injection = engine.get_state_for_injection()
    
    # Track injection tokens
    tracker.record(source="injection", raw=result["summary"],
                   filtered=injection)
    
    # Output summary (this is what the cron captures)
    print(f"🧠 Conscio Reflection — {datetime.now().strftime('%H:%M')} BRT")
    print(f"Mode: {engine.mode.value} | Model: {engine.model_info.name}")
    print(f"Confidence: {confidence:.0%}")
    print(f"Goals: {len(engine.goals.active_goals())} active")
    print(f"Anomalies: {len(anomalies)}")
    
    # v0.2 stats
    bus_stats = bus.stats()
    store_stats = store.stats()
    gain = tracker.gain()
    print(f"Events: {bus_stats['total_events']} | Indexed: {store_stats['source_count']} | Token savings: {gain['overall_saving_pct']:.1f}%")
    
    if engine.evolution.pending_proposals():
        print(f"⚠️ {len(engine.evolution.pending_proposals())} evolution proposals pending")
    print(f"\n📝 State Summary:\n{result['summary']}")
    print(f"\n💉 Context Injection ({engine._state.total_tokens_approx()} tokens):\n{injection}")


if __name__ == "__main__":
    main()
