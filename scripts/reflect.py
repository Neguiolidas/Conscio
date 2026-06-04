#!/usr/bin/env python3
"""
Conscio Reflection Script — Run by cron every 30 minutes.

Reads world state from Hermes consciousness data,
runs a reflection cycle, and outputs the state summary.
This output gets injected into the next cron prompt as context.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add repo to path
sys.path.insert(0, "/home/ubuntu/clawd/Repos/Conscio")

from conscio import ConsciousnessEngine
from conscio.models import ModelRegistry, ContextMode

# Configuration
STORAGE_PATH = Path.home() / ".hermes" / "consciousness"
MODEL_NAME = os.environ.get("CONSCIO_MODEL", "glm-5.1")

def gather_world_state() -> str:
    """
    Gather current world state from various sources.
    This is the PERCEIVE step — what does the agent see right now?
    """
    parts = []
    
    # System time
    now = datetime.now()
    parts.append(f"Time: {now.strftime('%Y-%m-%d %H:%M')} BRT")
    
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
    
    # Initialize engine
    engine = ConsciousnessEngine(
        model_name=MODEL_NAME,
        storage_path=STORAGE_PATH,
    )
    
    # PERCEIVE
    world_state = gather_world_state()
    anomalies = detect_anomalies()
    
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
    
    # Output summary (this is what the cron captures)
    print(f"🧠 Conscio Reflection — {datetime.now().strftime('%H:%M')} BRT")
    print(f"Mode: {engine.mode.value} | Model: {engine.model_info.name}")
    print(f"Confidence: {confidence:.0%}")
    print(f"Goals: {len(engine.goals.active_goals())} active")
    print(f"Anomalies: {len(anomalies)}")
    if engine.evolution.pending_proposals():
        print(f"⚠️ {len(engine.evolution.pending_proposals())} evolution proposals pending")
    print(f"\n📝 State Summary:\n{result['summary']}")
    print(f"\n💉 Context Injection ({engine._state.total_tokens_approx()} tokens):\n{injection}")


if __name__ == "__main__":
    main()
