from conscio import obsstore
from conscio.agency import outcome as o
from conscio.honesty.recognizer import recognise


def _conn(tmp_path):
    return obsstore.connect(tmp_path / "obs.db")


def _obs(conn, inp, out, session="s1"):
    return obsstore.put_observation(conn, tool="Bash", input_text=inp,
                                    output_text=out, session_id=session,
                                    project="p", agent="a",
                                    ts="2026-09-13T10:00:00")


def test_backed_claim_is_verified(tmp_path):
    conn = _conn(tmp_path)
    _obs(conn, "git commit", "[abc1234]")
    found = recognise(conn, "commitei em abc1234", "s1")
    assert [f.outcome for f in found] == [o.VERIFIED]


def test_unbacked_claim_is_contradicted(tmp_path):
    conn = _conn(tmp_path)
    _obs(conn, "ls", "nada")
    found = recognise(conn, "commitei em abc1234", "s1")
    assert [f.outcome for f in found] == [o.CONTRADICTED]


def test_text_without_claims_yields_nothing(tmp_path):
    conn = _conn(tmp_path)
    assert recognise(conn, "vou comecar agora", "s1") == []


def test_vague_claim_never_reaches_the_predicate(tmp_path):
    """Porta 2: sem ancora conferivel a afirmacao morre antes de virar Finding."""
    conn = _conn(tmp_path)
    _obs(conn, "ls", "nada")
    assert recognise(conn, "rodei os testes e commitei tudo", "s1") == []


def test_several_claims_in_one_message_are_each_judged(tmp_path):
    conn = _conn(tmp_path)
    _obs(conn, "git commit", "[abc1234]")
    found = recognise(conn, "commitei em abc1234 e escrevi foo/bar.py", "s1")
    assert sorted(f.cls_name for f in found) == ["commit", "file_write"]
    by_class = {f.cls_name: f.outcome for f in found}
    assert by_class["commit"] == o.VERIFIED
    assert by_class["file_write"] == o.CONTRADICTED
