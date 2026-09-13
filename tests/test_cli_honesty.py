"""Emenda A4: sem caminho de leitura, o registro de honestidade e
indistinguivel de nao existir para a persona do PRD.

O E3 daria conscio_ledger ao modo balanced, mas ele viaja em outro plano --
ate la a unica forma de ler os claims seria abrir o SQLite na mao, e o C5 pede
um CONTRADICTED detectado E registrado, com o caso escrito a mao.
"""
from conscio.cli import main
from conscio.honesty import verdicts as o
from conscio.honesty.store import ClaimStore


def test_recent_prints_the_recorded_claims(tmp_path, capsys):
    ClaimStore(tmp_path / "conscio.db").record(
        "s1", "commit", "abc1234", o.CONTRADICTED, "")
    assert main(["honesty", "recent", "--storage", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "CONTRADICTED" in out
    assert "abc1234" in out
    assert "commit" in out


def test_empty_store_says_so_instead_of_printing_nothing(tmp_path, capsys):
    """Saida vazia e indistinguivel de comando quebrado."""
    assert main(["honesty", "recent", "--storage", str(tmp_path)]) == 0
    assert "no claims" in capsys.readouterr().out.lower()


def test_a_space_without_the_table_is_not_an_error(tmp_path, capsys):
    """Espaco onde o hook nunca rodou: nada a mostrar, nao uma exception."""
    (tmp_path / "conscio.db").write_bytes(b"")
    assert main(["honesty", "recent", "--storage", str(tmp_path)]) == 0


def test_outcome_filter_narrows_the_listing(tmp_path, capsys):
    store = ClaimStore(tmp_path / "conscio.db")
    store.record("s1", "commit", "aaa1111", o.VERIFIED, "obs:1")
    store.record("s1", "push", "bbb2222", o.CONTRADICTED, "")
    assert main(["honesty", "recent", "--storage", str(tmp_path),
                 "--outcome", "CONTRADICTED"]) == 0
    out = capsys.readouterr().out
    assert "bbb2222" in out
    assert "aaa1111" not in out
