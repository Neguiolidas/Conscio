"""v4.6.7: the space rides on the card, and a blind republish never erases it."""
from __future__ import annotations

import json

import pytest

from conscio.liaison import directory

_ID = "cccccccc-3333-4333-8333-cccccccccccc"


@pytest.fixture
def relay_root(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    return tmp_path


def _space_on_card() -> str:
    card = directory.get(_ID) or {}
    return str(card.get("space", ""))


def test_blind_republish_preserves_and_explicit_overrides(relay_root):
    """A / A / B — the whole rule in one sequence.

    Step 2 is the reactor: a process that republishes the card without knowing
    any space. It must not erase what the server published, and it must not
    quietly replace it with the default.

    Step 3 is the server on a new space. The new value has to win, which is
    exactly where the one-word mistake bites: putting "space" in the same
    preservation loop as `halls` would keep answering A here, for ever.
    """
    a, b = str(relay_root / "space-a"), str(relay_root / "space-b")

    directory.publish_self(_ID, space=a)
    assert _space_on_card() == a

    directory.publish_self(_ID)                      # the reactor's republish
    assert _space_on_card() == a, "a blind republish erased the space"

    directory.publish_self(_ID, space=b)
    assert _space_on_card() == b, "the card froze on its first space"


def test_card_without_a_space_does_not_grow_an_empty_field(relay_root):
    """Absent and empty must stay the same thing to whoever reads the card.

    If every publish wrote `space: ""`, "has the field" would stop meaning
    "knows a space", and the legacy-card path would become untestable because
    the code could no longer produce a card without it.
    """
    directory.publish_self(_ID)
    assert "space" not in (directory.get(_ID) or {})


def test_the_published_space_is_what_the_resolver_reads(relay_root, monkeypatch):
    """The two halves meet: what the agent publishes is what the CLI resolves.

    Proving each half alone is how v4.6.6 nearly shipped a working function
    that nothing was wired to.
    """
    from conscio.space import resolve_live_space

    monkeypatch.setenv("CONSCIO_HOME", str(relay_root / "home"))
    monkeypatch.delenv("CONSCIO_SPACE", raising=False)
    monkeypatch.delenv("CONSCIO_SELF_ID", raising=False)

    # The space has to actually exist and actually belong to this agent: the
    # resolver verifies both, so a card alone is not enough to steer it.
    live = relay_root / "the-live-one"
    live.mkdir(parents=True, exist_ok=True)
    (live / "instance.json").write_text(
        json.dumps({"instance_id": _ID, "label": "t", "created_ts": 0.0}),
        encoding="utf-8")

    directory.publish_self(_ID, space=str(live))
    assert resolve_live_space() == (live, "card")
