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
    """CLI flag turns it on; otherwise the space decides; otherwise off.

    THE RULE THAT DECIDES EVERY CASE HERE, and it is the whole design:
    **consent is born from a present action of the user, never from a
    retroactive artefact.**

    A flag on the command line IS a present action — the user is invoking the
    server with it right now — so seeing one persists it. That write-through is
    what lets a hand-configured host heal itself: after the first boot the
    capability no longer depends on an argument the next update overwrites.

    Everything on the other side of that line is refused, however tempting.
    An earlier version's cached `.mcp.json` still carrying `--enable-relay` is
    the past, and with A3 in play its absence is ambiguous — it could be the
    user revoking or the bug erasing. An existing `liaison.db` is worse still:
    it exists because the agent RECEIVED A MESSAGE, not because anyone agreed
    to anything. Granting on either would resurrect the consent of whoever
    deleted the flag by hand, which until this release was the only way to
    revoke — exactly the population A3 hit.

    A capability that cannot be inferred is announced instead: the SessionStart
    hook says what looks lost and which command restores it. The cure for a
    silent failure is to make it speak, not to guess.
    """
    if cli_flag:
        try:
            write_capabilities(storage, read_capabilities(storage) | {name})
        except (OSError, ValueError):
            pass                      # persistir e conveniencia, nao o veredito
        return True
    return name in read_capabilities(storage)
