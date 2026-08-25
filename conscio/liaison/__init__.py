"""Liaison — same-host cross-agent control comms (v2.6.0).

Engine-free by contract: this package never imports conscio.engine. It carries
directed messages between agent instances through a shared SQLite mailbox under
$HERMES_HOME, and defines the pure hermes_review protocol (fingerprint +
request/verdict payloads). Nothing here executes, dispatches, or trusts.

v4.3.1 — ``conscio.liaison.tick``: private-cursor relay sweep + IMPORTANT
classification for host supervisors (systemd/cron). Unlike ``watcher`` (shared
per-peer ``watcher_state`` cursor that any mailbox writer may also advance),
``tick.sweep`` uses an opt-in private cursor file so multiple agents polling
the same mailbox never clobber each other's read position.
"""

from . import tick  # noqa: F401
from .tick import classify_important, classify_peer, sweep  # noqa: F401
