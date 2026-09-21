"""Where the live space is — one resolver, for everything a human types.

The package never knew where the live space was. Hooks get it right only because
the host injects ``--storage "${CLAUDE_PLUGIN_DATA}/space"`` into every line of
``hooks.json``; that correctness is borrowed from Claude Code and does not
survive contact with a bare shell. Everything a person types therefore fell
through to :func:`default_storage`, and answered — confidently — about a space
nobody runs.

The cure is not to teach the package to guess a host's layout. It is to have
*whoever owns a space publish where it is* (the directory card already travels
between agents and already carries everything else about them), and to give the
package one place that knows how to read that.

One resolver, for the same reason ``mailbox.resolve_db`` is one: two independent
resolutions is how one side starts writing somewhere the other side never reads
— which is, literally, the defect this module exists to close.

This lives here and not in ``noosphere/paths.py`` because reading a card would
close an import cycle: ``paths -> liaison.directory -> liaison.agents ->
liaison.mailbox -> paths`` (mailbox imports ``conscio_home`` as a pure leaf).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from .noosphere.paths import default_storage

__all__ = [
    "CARD_FIELD",
    "SELF_ID_ENV",
    "SPACE_ENV",
    "AmbiguousSpace",
    "LiveSpace",
    "resolve_live_space",
]

SPACE_ENV = "CONSCIO_SPACE"
SELF_ID_ENV = "CONSCIO_SELF_ID"
CARD_FIELD = "space"


class LiveSpace(NamedTuple):
    """The resolved space and *how* it was resolved.

    Provenance rides along with the path instead of living in a second
    ``explain()`` call: two calls answering the same question is how one of them
    drifts. Callers that only want the path take ``.path``; callers that must
    tell the operator which space they read take ``.source``, and none of them
    reconstructs it themselves.
    """

    path: Path
    source: str                    # explicit | env | card | default


class AmbiguousSpace(Exception):
    """Several agents published a space and nothing picked between them.

    Raised rather than resolved, because picking silently is precisely the
    disease: a confident answer about a space the operator did not choose. The
    candidates travel on the exception so the entrypoint can print them and let
    a human decide.
    """

    def __init__(self, candidates: list[tuple[str, Path]]):
        self.candidates = candidates
        listing = ", ".join(f"{cid[:8]}={path}" for cid, path in candidates)
        super().__init__(
            f"several agents published a space ({listing}); pass --storage, or "
            f"set {SELF_ID_ENV} to the instance you mean")


def _real(path: Path) -> Path:
    """Best-effort canonical form. `resolve()` does not need the path to exist,
    but a permission error or a symlink loop must not take the CLI down — an
    unresolvable path simply compares as itself."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def _published_spaces() -> list[tuple[str, Path, bool]]:
    """Cards that name a usable space, newest-first order NOT applied on purpose.

    Three filters, and each one catches what the others let through:

    *Points at the default.* Flagged, not dropped — it may not be SELECTED, but
    it still counts as an agent laying claim. Such a card is by construction an
    artifact of the very bug this module closes, and preferring it changes
    nothing, since falling through lands on the path it named anyway.

    It has to keep counting, though, and that was a real hole: dropping it
    outright meant that on a machine with one ghost and one live agent, the
    ghost vanished, the live agent became the sole candidate, and the operator
    working in the default space was silently handed somebody else's. Trading
    one confident answer about the wrong space for another is not a fix.

    *Space is gone.* Dropped. A deleted space is the one unambiguous sign that
    an agent is not merely idle. The default answers instead, and says so.

    *Space belongs to somebody else.* Dropped, via the existing
    ``space_is_cross_agent``: the card claims an agent owns a space, and the
    ``instance.json`` living there is the authority on whether that is true. A
    card that misnames its own space is not evidence about where to look.

    Note deliberately NOT used: card age. ``directory.is_live`` is "só para
    exibição — nunca para endereçar"; an agent idle for eleven minutes has not
    stopped owning its space, and a week of holiday would leave an old card over
    a perfectly live one.
    """
    from .installer.spaces import space_is_cross_agent
    from .liaison import directory  # deferred: keeps this module light

    # Resolved on both sides: a space reached through a symlink (a moved home,
    # a mounted disk) is the same space, and comparing unresolved paths would
    # let a ghost slip past the flag below while filter three, which DOES
    # resolve, disagreed with it.
    default_real = _real(default_storage().expanduser())
    out: list[tuple[str, Path, bool]] = []
    for card in directory.peers():
        raw = str(card.get(CARD_FIELD, "") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        cid = str(card.get("instance_id", ""))
        if not path.exists():
            continue
        if space_is_cross_agent(str(path), cid):
            continue
        out.append((cid, path, _real(path) == default_real))
    return out


def _from_cards(self_id: str = "") -> Path | None:
    """The card rung: the published space, when exactly one agent is meant.

    Ambiguity is counted over every surviving card, including one that names
    the default; selection happens only among the rest. A named agent — by
    flag or by environment — outranks all of it, which is what keeps a fleet
    script or a systemd unit working on a machine with several agents.
    """
    candidates = _published_spaces()
    if not candidates:
        return None

    if self_id:
        mine = [p for cid, p, _ in candidates if cid == self_id]
        if mine:
            return mine[0]

    if len(candidates) > 1:
        raise AmbiguousSpace([(cid, p) for cid, p, _ in candidates])

    _cid, path, is_default = candidates[0]
    # The sole card names the default: fall through to it by the front door, so
    # the source reads "default" rather than dressing the same path as a card.
    return None if is_default else path


def resolve_live_space(
        explicit: str | os.PathLike[str] | None = None,
        self_id: str = "") -> LiveSpace:
    """Resolve the space this command should act on.

    Precedence, and every rung is declared rather than discovered:

    1. ``explicit`` — the operator named it; nothing argues with that.
    2. ``CONSCIO_SPACE`` — the environment named it.
    3. the directory card — the agent that owns a space published where it is,
       with ``self_id`` (a ``--self-id`` flag, falling back to the environment)
       naming which agent is meant when more than one published.
    4. ``default_storage()`` — and the caller is told, via ``.source``, that this
       is what happened. The default is no longer invisible.

    Raises :class:`AmbiguousSpace` when several agents published and nothing
    chose between them.
    """
    if explicit:
        return LiveSpace(Path(explicit).expanduser(), "explicit")

    from_env = os.environ.get(SPACE_ENV, "").strip()
    if from_env:
        return LiveSpace(Path(from_env).expanduser(), "env")

    from_card = _from_cards(
        self_id or os.environ.get(SELF_ID_ENV, "").strip())
    if from_card is not None:
        return LiveSpace(from_card, "card")

    return LiveSpace(default_storage(), "default")
