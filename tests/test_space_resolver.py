"""v4.6.7: one resolver for the live space, and it never guesses silently."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conscio.liaison import directory
from conscio.space import (
    CARD_FIELD,
    SELF_ID_ENV,
    SPACE_ENV,
    AmbiguousSpace,
    resolve_live_space,
)

_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


@pytest.fixture
def space_env(tmp_path, monkeypatch):
    """A machine of our own: neutral home and an empty relay directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("CONSCIO_HOME", str(home))
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    monkeypatch.delenv(SPACE_ENV, raising=False)
    monkeypatch.delenv(SELF_ID_ENV, raising=False)
    return tmp_path


def _make_space(path: Path, owner: str) -> Path:
    """A real space on disk, owned by `owner` — the state a card asserts."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "instance.json").write_text(
        json.dumps({"instance_id": owner, "label": f"t-{owner[:8]}",
                    "created_ts": 0.0}), encoding="utf-8")
    return path


def _card(instance_id: str, space: str | Path | None, *,
          on_disk: bool = True, owner: str | None = None) -> None:
    """Publish a card. By default the space it names really exists and really
    belongs to the card's agent — anything else is the exception being tested."""
    card = {"instance_id": instance_id, "capabilities": ["relay"]}
    if space is not None:
        card[CARD_FIELD] = str(space)
        if on_disk:
            _make_space(Path(space), owner or instance_id)
    directory.peers_dir().mkdir(parents=True, exist_ok=True)
    (directory.peers_dir() / f"{instance_id}.json").write_text(
        json.dumps(card), encoding="utf-8")


def test_precedence_runs_explicit_env_card_default(space_env, monkeypatch):
    """All four rungs, each one outranking the next."""
    named, from_env, from_card = (space_env / n for n in
                                  ("named", "env", "card"))
    _card(_A, from_card)

    assert resolve_live_space(named) == (named, "explicit")

    monkeypatch.setenv(SPACE_ENV, str(from_env))
    assert resolve_live_space() == (from_env, "env")
    # explicit still outranks the environment
    assert resolve_live_space(named).source == "explicit"

    monkeypatch.delenv(SPACE_ENV)
    assert resolve_live_space() == (from_card, "card")


def test_default_is_reached_but_never_silent(space_env):
    """No card, no env: the default answers — and says that it is the default."""
    resolved = resolve_live_space()
    assert resolved.source == "default"
    assert resolved.path.name == "consciousness"


def test_card_naming_a_deleted_space_falls_through(space_env):
    """A space that is gone is the one unambiguous sign of a dead agent.

    Age cannot say this: a card is equally old whether its owner is on holiday
    or gone for good. The deleted directory is what distinguishes them.
    """
    _card(_A, space_env / "was-here", on_disk=False)
    assert resolve_live_space().source == "default"


def test_card_naming_someone_elses_space_is_refused(space_env):
    """The instance.json living in a space is the authority on who owns it.

    A card that misnames its own space is not evidence about where to look —
    and this is the case the default-space filter alone would let through.
    """
    _card(_A, space_env / "belongs-to-b", owner=_B)
    assert resolve_live_space().source == "default"


def test_card_naming_the_default_space_is_ignored(space_env):
    """The ghost this bug manufactures does not get to steer the resolver.

    Dropping it cannot change the outcome for its own case: falling through
    lands on the very path the card named.
    """
    from conscio.noosphere.paths import default_storage

    # The ghost is structurally perfect: its space exists and its instance.json
    # matches its card. Ownership and existence both pass — only "it names the
    # default" removes it. This is exactly why the three filters are three.
    _card(_A, default_storage())
    assert resolve_live_space().source == "default"


def test_two_published_spaces_refuse_instead_of_picking(space_env):
    """Ambiguity is refused with the candidates, never resolved by recency."""
    _card(_A, space_env / "one")
    _card(_B, space_env / "two")
    with pytest.raises(AmbiguousSpace) as excinfo:
        resolve_live_space()
    assert {cid for cid, _ in excinfo.value.candidates} == {_A, _B}
    assert "--storage" in str(excinfo.value)


def test_self_id_breaks_the_tie(space_env, monkeypatch):
    """The common multi-agent case resolves instead of refusing."""
    mine = space_env / "mine"
    _card(_A, mine)
    _card(_B, space_env / "theirs")
    monkeypatch.setenv(SELF_ID_ENV, _A)
    assert resolve_live_space() == (mine, "card")


def test_legacy_card_without_the_field_degrades(space_env):
    """C8: the card every machine already has on disk has no such field."""
    _card(_A, None)
    assert resolve_live_space().source == "default"


def test_explicit_beats_even_an_ambiguous_directory(space_env):
    """A named space is an answer; ambiguity elsewhere must not override it."""
    _card(_A, space_env / "one")
    _card(_B, space_env / "two")
    assert resolve_live_space(space_env / "named").source == "explicit"


# ── os pontos cegos que o Gemini achou lendo os commits (review hostil)

def test_a_card_on_the_default_still_counts_as_a_claim(space_env):
    """Dropping it outright traded one wrong confident answer for another.

    With a ghost on the default and one live agent, dropping the ghost left a
    single candidate — so the operator working in the default space was handed
    somebody else's, silently. It may not be selected; it must still be counted.
    """
    from conscio.noosphere.paths import default_storage

    _card(_A, default_storage())
    _card(_B, space_env / "somebody-else")
    with pytest.raises(AmbiguousSpace):
        resolve_live_space()


def test_self_id_arrives_by_flag_not_only_by_environment(space_env):
    """A generated systemd unit carries --self-id and no environment.

    Reading only the env meant such a unit died at boot on a multi-agent
    machine — carrying the answer to the ambiguity but unable to use it.
    """
    mine = space_env / "mine"
    _card(_A, mine)
    _card(_B, space_env / "theirs")
    assert resolve_live_space(self_id=_A) == (mine, "card")


def test_a_named_agent_may_choose_the_default_space(space_env):
    """Being flagged is not being forbidden: an explicit choice still wins."""
    from conscio.noosphere.paths import default_storage

    _card(_A, default_storage())
    _card(_B, space_env / "other")
    assert resolve_live_space(self_id=_A).path == default_storage()


def test_a_symlinked_home_does_not_hide_the_ghost(tmp_path, monkeypatch):
    """Comparing unresolved paths let the ghost through a moved home.

    Filter three already resolved; this comparison did not, so the two
    disagreed about whether a path was the default.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv("CONSCIO_HOME", str(link))
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    monkeypatch.delenv(SPACE_ENV, raising=False)
    monkeypatch.delenv(SELF_ID_ENV, raising=False)

    _card(_A, real / "consciousness")          # the same place, spelled differently
    assert resolve_live_space().source == "default"


def test_an_all_whitespace_argument_is_not_a_space_name(space_env):
    """`--storage "   "` would otherwise name a space with three spaces.

    Note what this does NOT cover: a caller that wrapped an empty string before
    calling. `Path("")` is `PosixPath(".")` from the moment it is built, and no
    check here can tell that from someone who meant the working directory. That
    one is held by the contract, not by this guard.
    """
    assert resolve_live_space("   ").source == "default"
    assert resolve_live_space("\t\n ").source == "default"
