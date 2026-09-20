"""Opt-in capabilities persisted in the space.

Mirrors what ``modes.py`` does with ``mcp_mode``, and exists for one measured
reason: a plugin update recreates the cached ``.mcp.json`` from the asset and
wipes any argument the installer had written there. A capability that lives in
a file the update overwrites is a capability that silently disarms itself.

Precedence differs from ``resolve_mode`` ON PURPOSE. For the mode, the
persisted value beats the CLI so an update never shrinks a host that already
chose. These are ``store_true`` flags: absence means "unspecified", never
"off", so the flag can only turn a capability ON.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

CAPABILITIES: tuple[str, ...] = ("relay", "halls")

_FILENAME = "mcp_capabilities"


def capabilities_path(storage) -> Path:
    return Path(storage).expanduser() / _FILENAME


def read_capabilities(storage) -> set[str]:
    """The persisted set, or empty when absent, unreadable or unknown."""
    try:
        raw = capabilities_path(storage).read_text("utf-8")
    except (OSError, TypeError, ValueError):
        return set()
    return {line.strip() for line in raw.splitlines()
            if line.strip() in CAPABILITIES}


def write_capabilities(storage, caps: Iterable[str]) -> None:
    """Persist ``caps`` atomically. Raises ValueError on an unknown name."""
    wanted = sorted(set(caps))
    unknown = [c for c in wanted if c not in CAPABILITIES]
    if unknown:
        raise ValueError(f"unknown capability {unknown!r}; "
                         f"expected any of {CAPABILITIES}")
    path = capabilities_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(wanted) + "\n", "utf-8")
    tmp.replace(path)


def resolve_capability(storage, name: str, cli_flag: bool) -> bool:
    """CLI flag turns it on; otherwise the space decides; otherwise off."""
    return bool(cli_flag) or name in read_capabilities(storage)
