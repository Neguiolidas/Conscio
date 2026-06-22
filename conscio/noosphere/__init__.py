# conscio/noosphere/__init__.py
"""Noosphere — same-host skill sharing (v2.2.0).

Engine-free by contract: this package never imports conscio.engine,
conscio.agency.skills (SkillLibrary), or conscio.agency.tools (ToolRegistry).
It publishes locally-proven skills to a host-shared noosphere.db and imports
foreign skills into a per-instance quarantine. Nothing imported is served,
executed, promoted, or trusted in v2.2.0."""
