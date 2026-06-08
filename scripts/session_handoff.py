#!/usr/bin/env python3
"""
session_handoff.py — Cron wrapper for Conscio session handoff.

Uses the Conscio-integrated pipeline (session_lifecycle.py) instead of
the standalone extraction. This gives us:
 - Conscio engine enrichment (world model, goals, confidence)
 - EventBus emission + ContentStore indexing
 - Post-session reflection + dream
 - Consistent format with the hook-generated heartbeat/handoff

Called by the hermet-session-handoff cron job as a safety net —
the primary generation should happen via the session:end/reset hook,
but this catches cases where the hook didn't fire (gateway restart, etc).

Exit codes:
 0 — success (handoff saved)
 1 — no session data found
 2 — error
"""

import sys
from pathlib import Path

# Add Conscio repo to path
CONSCIO_REPO = Path.home() / "clawd" / "Repos" / "Conscio"
if str(CONSCIO_REPO) not in sys.path:
    sys.path.insert(0, str(CONSCIO_REPO))

from conscio.session_lifecycle import (
    record_session_lifecycle,
    HEARTBEAT_PATH,
    HANDOFF_PATH,
)


def main():
    # Use get_latest_session as fallback (no specific session_id from cron)
    # session_lifecycle.py handles this: when context has no session_id,
    # it falls back to get_latest_session()
    context = {
        "platform": "cron",
        "user_id": "",
        "session_key": "cron:handoff",
        "session_id": "",  # empty → triggers get_latest_session fallback
    }

    summary = record_session_lifecycle(
        event_type="session:end",
        context=context,
        engine=None,
    )

    if summary is None:
        print("No session data to summarize")
        sys.exit(1)

    hb_size = HEARTBEAT_PATH.stat().st_size if HEARTBEAT_PATH.exists() else 0
    ho_size = HANDOFF_PATH.stat().st_size if HANDOFF_PATH.exists() else 0

    print(
        f"OK: handoff saved "
        f"({len(summary.intents)} intents, {len(summary.actions)} actions, "
        f"{len(summary.reasoning)} reasoning, {ho_size} bytes) + "
        f"heartbeat ({hb_size} bytes) "
        f"· conscio: {len(summary.active_goals)} goals, "
        f"conf={summary.meta_confidence:.2f}, "
        f"topics={summary.topics}"
    )


if __name__ == "__main__":
    main()
