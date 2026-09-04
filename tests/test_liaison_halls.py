# tests/test_liaison_halls.py
"""Agent's Hall sem roster: membership mora no cartão do próprio agente e o
doc do hall (escrito só pelo dono) carrega as funções. Nenhum sqlite aqui —
exceto o teste da migração do legado."""
import time

import pytest

from conscio.liaison import directory, halls


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))


def _card(cid, **kw):
    directory.publish({"instance_id": cid, "spool": cid, "url": "",
                       "updated_at": time.time(), **kw})


def test_join_writes_only_my_own_card():
    _card("agent-a"); _card("agent-b")
    assert halls.join(instance_id="agent-a", hall_id="owner--team") is True
    assert directory.get("agent-a")["halls"] == ["owner--team"]
    assert "halls" not in directory.get("agent-b")      # não toco em cartão alheio


def test_concurrent_joins_never_lose_a_member():
    for cid in ("a1", "a2", "a3"):
        _card(cid)
        halls.join(instance_id=cid, hall_id="owner--team")
    got = {m["instance_id"] for m in halls.members_of("owner--team")}
    assert got == {"a1", "a2", "a3"}     # sem roster compartilhado, sem last-writer-wins


def test_everyone_enters_as_executor():
    _card("a1")
    halls.join(instance_id="a1", hall_id="owner--team")
    assert halls.members_of("owner--team")[0]["function"] == "executor"


def test_leave_removes_only_the_membership():
    _card("a1", modelo="opus")
    halls.join(instance_id="a1", hall_id="d--h")
    assert halls.leave(instance_id="a1", hall_id="d--h") is True
    assert halls.members_of("d--h") == []
    assert directory.get("a1")["modelo"] == "opus"      # RMW não come o resto do cartão


def test_dead_member_only_disappears_when_asked():
    _card("alive-one"); _card("dead-one")
    halls.join(instance_id="alive-one", hall_id="d--h")
    halls.join(instance_id="dead-one", hall_id="d--h")
    # envelhecer DEPOIS de entrar: join republica o cartão e é, ele próprio,
    # sinal de vivacidade — quem entra está vivo naquele instante
    dead = directory.get("dead-one")
    dead["updated_at"] = time.time() - 10 * 3600
    directory.publish(dead)
    assert len(halls.members_of("d--h")) == 2
    alive = halls.members_of("d--h", alive_only=True)
    assert [m["instance_id"] for m in alive] == ["alive-one"]


def test_create_hall_is_owner_written_and_dedups():
    _card("boss")
    h = halls.create_hall(owner="boss", name="Team A")
    assert h["hall_id"] == "boss--team-a"
    assert halls.create_hall(owner="boss", name="Team A") is None
    members = halls.members_of("boss--team-a")
    assert [m["instance_id"] for m in members] == ["boss"]
    assert members[0]["function"] == "leader"          # o criador nasce líder


def test_hall_id_too_long_fails_loud():
    _card("boss")
    with pytest.raises(ValueError):        # I1: 64 chars, não None calado
        halls.create_hall(owner="o" * 40, name="n" * 40)


def test_create_hall_without_a_published_card_fails_loud():
    with pytest.raises(ValueError):        # dono invisível = hall sem dono
        halls.create_hall(owner="ghost", name="Team")


def test_leader_assigns_function_without_touching_anyone_card():
    _card("boss"); _card("other")
    halls.create_hall(owner="boss", name="Council")
    halls.join(instance_id="other", hall_id="boss--council")
    halls.set_function(hall_id="boss--council", owner="boss",
                       instance_id="other", function="reviewer")
    m = {x["instance_id"]: x for x in halls.members_of("boss--council")}
    assert m["other"]["function"] == "reviewer"
    assert directory.get("other")["halls"] == ["boss--council"]   # cartão intacto
    assert halls.get_hall("boss--council")["functions"]["other"] == "reviewer"


def test_only_the_owner_assigns():
    _card("boss"); _card("intruder")
    halls.create_hall(owner="boss", name="Council")
    with pytest.raises(PermissionError):
        halls.set_function(hall_id="boss--council", owner="intruder",
                           instance_id="intruder", function="leader")


def test_unknown_function_fails_loud():
    _card("boss"); _card("other")
    halls.create_hall(owner="boss", name="Council")
    with pytest.raises(ValueError):        # NÃO vira "executor" em silêncio
        halls.set_function(hall_id="boss--council", owner="boss",
                           instance_id="other", function="reviewerr")
    assert halls.normalize_function("Reviewer") == "reviewer"     # caixa e acento


def test_imported_member_never_published_anything():
    _card("boss"); _card("recruit")
    halls.create_hall(owner="boss", name="Council")
    assert halls.import_members(hall_id="boss--council", owner="boss",
                                instance_ids=["recruit"]) == 1
    got = {m["instance_id"]: m for m in halls.members_of("boss--council")}
    assert sorted(got) == ["boss", "recruit"]
    assert got["recruit"]["imported"] is True
    assert "halls" not in directory.get("recruit")   # ninguém escreveu no cartão dele


def test_declining_beats_being_imported():
    _card("boss"); _card("recruit")
    halls.create_hall(owner="boss", name="Council")
    halls.import_members(hall_id="boss--council", owner="boss",
                         instance_ids=["recruit"])
    assert halls.leave(instance_id="recruit", hall_id="boss--council") is True
    ids = [m["instance_id"] for m in halls.members_of("boss--council")]
    assert ids == ["boss"]                           # autonomia: recusa é minha


def test_rejoining_clears_the_refusal():
    _card("boss"); _card("recruit")
    halls.create_hall(owner="boss", name="Council")
    halls.import_members(hall_id="boss--council", owner="boss",
                         instance_ids=["recruit"])
    halls.leave(instance_id="recruit", hall_id="boss--council")
    halls.join(instance_id="recruit", hall_id="boss--council")
    card = directory.get("recruit")
    assert card["halls"] == ["boss--council"] and card["halls_declined"] == []
    ids = [m["instance_id"] for m in halls.members_of("boss--council")]
    assert ids == ["boss", "recruit"]


def test_transfer_moves_authority_but_never_the_id():
    _card("boss"); _card("heir")
    halls.create_hall(owner="boss", name="Council")
    halls.join(instance_id="heir", hall_id="boss--council")
    assert halls.transfer_owner(hall_id="boss--council", current_owner="boss",
                                new_owner="heir") is True
    doc = halls.get_hall("boss--council")
    assert doc["owner"] == "heir" and doc["hall_id"] == "boss--council"
    halls.set_function(hall_id="boss--council", owner="heir",
                       instance_id="boss", function="reviewer")   # já manda
    with pytest.raises(PermissionError):
        halls.transfer_owner(hall_id="boss--council", current_owner="boss",
                             new_owner="boss")                    # não manda mais


def test_fanout_reaches_every_member_but_the_sender():
    for cid in ("a1", "a2", "a3"):
        _card(cid); halls.join(instance_id=cid, hall_id="d--h")
    sent = []
    n = halls.send_to_hall(from_instance="a1", hall_id="d--h", type="relay",
                           payload={"x": 1},
                           send=lambda **kw: sent.append(kw["to_instance"]))
    assert n == 2 and set(sent) == {"a2", "a3"}


def test_one_broken_member_never_aborts_the_rest():
    for cid in ("a1", "a2", "a3"):
        _card(cid); halls.join(instance_id=cid, hall_id="d--h")

    def _flaky(**kw):
        if kw["to_instance"] == "a2":
            raise OSError("spool cheio")

    n = halls.send_to_hall(from_instance="a1", hall_id="d--h", type="relay",
                           payload={}, send=_flaky)
    assert n == 1                          # a3 recebe mesmo com a2 quebrado


def test_fanout_can_address_a_single_function():
    _card("boss")
    halls.create_hall(owner="boss", name="Council")
    for cid, fn in (("r1", "reviewer"), ("r2", "reviewer"), ("p1", "researcher")):
        _card(cid)
        halls.join(instance_id=cid, hall_id="boss--council")
        halls.set_function(hall_id="boss--council", owner="boss",
                           instance_id=cid, function=fn)
    sent = []
    n = halls.send_to_hall(from_instance="boss", hall_id="boss--council",
                           type="relay", payload={}, function="reviewer",
                           send=lambda **kw: sent.append(kw["to_instance"]))
    assert n == 2 and set(sent) == {"r1", "r2"}


def test_invite_only_hall_never_delivers_to_an_outsider():
    _card("boss"); _card("guest"); _card("stranger")
    halls.create_hall(owner="boss", name="Closed", policy="invite",
                      invited=["guest"])
    for cid in ("guest", "stranger"):
        halls.join(instance_id=cid, hall_id="boss--closed")
    sent = []
    n = halls.send_to_hall(from_instance="boss", hall_id="boss--closed",
                           type="relay", payload={},
                           send=lambda **kw: sent.append(kw["to_instance"]))
    assert n == 1 and sent == ["guest"]


def test_message_says_which_hall_it_came_from():
    _card("a1"); _card("a2")
    for cid in ("a1", "a2"):
        halls.join(instance_id=cid, hall_id="d--h")
    got = []
    halls.send_to_hall(from_instance="a1", hall_id="d--h", type="relay",
                       payload={}, send=lambda **kw: got.append(kw["hall"]))
    assert got == [{"id": "d--h", "function": "executor"}]


def test_one_agent_in_many_halls_never_mixes_them():
    _card("a1"); _card("a2")
    for h in ("d--sec", "d--review"):
        for cid in ("a1", "a2"):
            halls.join(instance_id=cid, hall_id=h)
    assert directory.get("a1")["halls"] == ["d--review", "d--sec"]
    halls.leave(instance_id="a1", hall_id="d--sec")
    assert [m["instance_id"] for m in halls.members_of("d--sec")] == ["a2"]
    assert [m["instance_id"] for m in halls.members_of("d--review")] == ["a1", "a2"]


def test_list_halls_filters_by_owner():
    _card("boss"); _card("other")
    halls.create_hall(owner="boss", name="One")
    halls.create_hall(owner="other", name="Two")
    assert [d["hall_id"] for d in halls.list_halls(owner="boss")] == ["boss--one"]
    assert len(halls.list_halls()) == 2


def test_migrate_from_db_only_takes_what_is_mine(tmp_path):
    import sqlite3
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE halls (hall_id TEXT PRIMARY KEY, nome TEXT,"
                 " dono TEXT, criado_em REAL)")
    conn.execute("CREATE TABLE hall_members (hall_id TEXT, instance_id TEXT,"
                 " papel TEXT, entrou_em REAL)")
    conn.execute("INSERT INTO halls VALUES ('me--team','Team','me',1.0)")
    conn.execute("INSERT INTO hall_members VALUES ('me--team','me','dono',1.0)")
    conn.execute("INSERT INTO hall_members VALUES ('me--team','other','membro',1.0)")
    conn.commit(); conn.close()
    _card("me")
    assert halls.migrate_from_db(legacy, "me") == 1     # a linha do outro é do outro
    assert directory.get("me")["halls"] == ["me--team"]
    doc = halls.get_hall("me--team")
    assert doc["name"] == "Team" and doc["functions"]["me"] == "leader"
    assert halls.migrate_from_db(legacy, "me") == 0     # idempotente


def test_migrate_survives_a_missing_db_and_a_missing_schema(tmp_path):
    import sqlite3
    _card("me")
    assert halls.migrate_from_db(tmp_path / "does-not-exist.db", "me") == 0
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert halls.migrate_from_db(empty, "me") == 0     # sem schema, sem crash
