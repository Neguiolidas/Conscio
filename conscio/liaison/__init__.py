"""Liaison — same-host cross-agent control comms (v2.6.0).

Engine-free by contract: this package never imports conscio.engine. It carries
directed messages between agent instances through a shared SQLite mailbox under
$HERMES_HOME, and defines the pure hermes_review protocol (fingerprint +
request/verdict payloads). Nothing here executes, dispatches, or trusts."""
