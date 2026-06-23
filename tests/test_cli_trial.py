# tests/test_cli_trial.py
import pytest

from conscio import cli
from conscio.agency.trial import TrialOutcome, TrialRefusal


def test_passed_prints_and_returns_0(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_run_trial",
                        lambda **kw: TrialOutcome(True, "passed", "", []))
    rc = cli.main(["trial", "--quarantine", "1", "--enable-trial",
                   "--storage", "/tmp/x"])
    assert rc == 0
    assert "TRIAL PASSED" in capsys.readouterr().out


def test_failed_prints_and_returns_0(capsys, monkeypatch):
    monkeypatch.setattr(
        cli, "_run_trial",
        lambda **kw: TrialOutcome(False, "exec_fail:fs_read", "boom", []))
    rc = cli.main(["trial", "--quarantine", "1", "--enable-trial"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TRIAL FAILED" in out and "exec_fail:fs_read" in out


def test_refusal_returns_1(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_run_trial",
                        lambda **kw: TrialRefusal("trial requires an adapter"))
    rc = cli.main(["trial", "--quarantine", "1", "--enable-trial"])
    assert rc == 1
    assert "error:" in capsys.readouterr().out


def test_enable_trial_flag_forwarded(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_run_trial",
                        lambda **kw: seen.update(kw) or TrialOutcome(
                            True, "passed", "", []))
    cli.main(["trial", "--quarantine", "7", "--enable-trial"])
    assert seen["enable_trial"] is True and seen["quarantine_id"] == 7


def test_enable_trial_default_false(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_run_trial",
                        lambda **kw: seen.update(kw) or TrialRefusal("off"))
    cli.main(["trial", "--quarantine", "7"])
    assert seen["enable_trial"] is False


def test_missing_quarantine_is_argparse_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["trial", "--enable-trial"])
    assert exc.value.code == 2
