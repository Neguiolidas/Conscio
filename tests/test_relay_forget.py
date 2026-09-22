"""v4.6.7: retiring an address that outlived whatever published it."""
from __future__ import annotations

import json

import pytest

from conscio.liaison import directory, relay_cli

_DEAD = "6fee5050-0000-4000-8000-000000000001"
_MINE = "11111111-0000-4000-8000-000000000002"


@pytest.fixture
def square(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    monkeypatch.delenv("CONSCIO_SELF_ID", raising=False)
    directory.peers_dir().mkdir(parents=True, exist_ok=True)
    for iid in (_DEAD, _MINE):
        (directory.peers_dir() / f"{iid}.json").write_text(
            json.dumps({"instance_id": iid, "capabilities": ["relay"],
                        "updated_at": 0.0}), encoding="utf-8")
    return tmp_path


def _run(*argv: str) -> int:
    return relay_cli.main(["forget", *argv])


def test_forgetting_removes_only_that_card(square, capsys):
    assert _run(_DEAD) == 0
    assert directory.get(_DEAD) is None
    assert directory.get(_MINE) is not None, "it took a bystander with it"
    assert "forgot" in capsys.readouterr().out


def test_the_space_and_identity_are_untouched(square, tmp_path):
    """Forgetting is about the address, not the agent.

    This is the safety story: the command cannot destroy an agent's data, and
    a live peer simply republishes. It retires the dead, it does not kill.
    """
    space = tmp_path / "some-space"
    space.mkdir()
    (space / "instance.json").write_text('{"instance_id": "x"}',
                                         encoding="utf-8")
    assert _run(_DEAD) == 0
    assert (space / "instance.json").exists()


def test_an_unknown_id_is_reported_not_silently_accepted(square, capsys):
    """Saying "done" about a card that was never there teaches nothing."""
    assert _run("99999999-0000-4000-8000-00000000dead") == 1
    assert "no card" in capsys.readouterr().out


def test_a_malformed_id_is_refused_before_touching_the_filesystem(square):
    """`valid_id` is the path guard: an id becomes a filename, and `../` in one
    would reach outside the directory."""
    assert _run("../../etc/passwd") == 2
    assert directory.get(_DEAD) is not None


def test_forgetting_yourself_warns_but_is_allowed(square, capsys, monkeypatch):
    """Allowed because a running agent republishes; warned because a stopped
    one does not, and the operator should know which case they are in."""
    monkeypatch.setenv("CONSCIO_SELF_ID", _MINE)
    assert _run(_MINE) == 0
    assert "your own card" in capsys.readouterr().err
