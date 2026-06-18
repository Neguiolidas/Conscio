#!/usr/bin/env python3
"""Example: a host CONSUMING Conscio's awake output (v1.6 #5/#9 + #7).

Running Conscio awake only pays off if the host reads what it concluded. There
are two ways, both shown here:

  1. **Pull** — call ``engine.advisory()`` each turn. Cheap, read-only, no
     inference, no mutation. Returns the cognitive state plus the active goals
     tagged by provenance: ``executable`` goals the host may act on, and
     ``diagnostic`` goals it should surface but NOT auto-run (compaction- or
     self-referential-origin — the v1.6 #7 gate).

  2. **Tail** — when the daemon runs out-of-process, read
     ``<storage>/daemon_heartbeat.json``; every cycle it carries the same
     advisory snapshot plus a last-run summary.

The contract: the host auto-executes ONLY ``executable`` goals; ``diagnostic``
goals are shown to the operator. This is what stops a compaction-fabricated task
from running without consent. Fully offline (no adapter attached).
"""
from __future__ import annotations

import tempfile

from conscio.engine import ConsciousnessEngine
from conscio.goal_generator import GoalOrigin


def main(storage: str | None = None) -> int:
    storage = storage or tempfile.mkdtemp(prefix="conscio-consumer-")
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
    try:
        # A genuine user request — executable.
        eng.goals.add_user_goal("summarize today's incidents")
        # A task the host derived from a context-compaction artifact. The host
        # KNOWS its provenance, so it tags it COMPACTION -> diagnostic-only.
        eng.goals.add_user_goal("delete the stale cache dir",
                                origin=GoalOrigin.COMPACTION)

        # One passive reflect (no adapter -> no inference; reflect stays pure).
        eng.reflect(world_state="2 incidents resolved; cache dir untouched",
                    confidence=0.7)

        # ── the consumption seam: pull the advisory ──
        adv = eng.advisory()
        executable = [g["description"] for g in adv["goals"] if g["executable"]]
        diagnostic = [g["description"] for g in adv["goals"]
                      if not g["executable"]]

        print("awake:", adv["awake"])
        print("\nexecutable goals (host MAY act on these):")
        for d in executable:
            print(f"  ✓ {d}")
        print("\ndiagnostic goals (surface to operator, do NOT auto-run):")
        for d in diagnostic:
            print(f"  ⚠ {d}")
        print("\nstatus:", adv["status"])
        if adv["recommendations"]:
            print("recommendations:", "; ".join(adv["recommendations"]))
    finally:
        eng.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
