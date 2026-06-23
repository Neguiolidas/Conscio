# tests/test_cli_promote.py
import pytest

from conscio import cli
from conscio.agency.promote import PromoteRefusal, PromoteResult


def test_promote_rc0_on_result(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run_promote",
                        lambda **kw: PromoteResult(7, 3, 0))
    rc = cli.main(["promote", "--quarantine", "1", "--enable-promote"])
    assert rc == 0
    assert "PROMOTED skill #7" in capsys.readouterr().out


def test_promote_rc1_on_refusal(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run_promote",
                        lambda **kw: PromoteRefusal("nope"))
    rc = cli.main(["promote", "--quarantine", "1", "--enable-promote"])
    assert rc == 1
    assert "PROMOTE REFUSED: nope" in capsys.readouterr().out


def test_promote_rc1_on_exception(monkeypatch, capsys):
    def boom(**kw):
        raise RuntimeError("wiring failed")
    monkeypatch.setattr(cli, "_run_promote", boom)
    rc = cli.main(["promote", "--quarantine", "1", "--enable-promote"])
    assert rc == 1
    assert "error: wiring failed" in capsys.readouterr().out


def test_promote_requires_quarantine():
    with pytest.raises(SystemExit):
        cli.main(["promote", "--enable-promote"])


def test_promote_disabled_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli, "_run_promote",
        lambda **kw: seen.update(kw) or PromoteRefusal("x"))
    cli.main(["promote", "--quarantine", "5"])
    assert seen["enable_promote"] is False
    assert seen["quarantine_id"] == 5
