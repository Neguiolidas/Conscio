from conscio.mcp.server import Bindings, _arg_parser


class _Engine:
    def __init__(self, awake, host_act):
        self.awake = awake
        self._host_act = host_act

    @property
    def host_act(self):
        return self._host_act

    def advisory(self):
        return {"ok": True}


class _Seen:
    pass


class _Reg:
    def names(self):
        return []


class _HA:
    def __init__(self):
        self.calls = 0
        self.registry = _Reg()           # conscio_meta() reads registry.names()

    def pending(self, n):
        self.calls += 1
        return []


def _bind(*, awake, host_act, reviewers, auto_review):
    eng = _Engine(awake, host_act)
    return Bindings(eng, _Seen(), auto_review=auto_review,
                    hermes_review=True, reviewers=reviewers,
                    self_instance_id="me", liaison_db=None)


def test_flag_parses():
    args = _arg_parser().parse_args(["--auto-review"])
    assert args.auto_review is True


def test_meta_exposes_flag():
    b = _bind(awake=True, host_act=_HA(), reviewers=("r",), auto_review=True)
    assert b.conscio_meta()["auto_review_enabled"] is True


def test_inert_when_asleep():
    ha = _HA()
    b = _bind(awake=False, host_act=ha, reviewers=("r",), auto_review=True)
    b._maybe_auto_apply()
    assert ha.calls == 0


def test_inert_without_reviewers():
    ha = _HA()
    b = _bind(awake=True, host_act=ha, reviewers=(), auto_review=True)
    b._maybe_auto_apply()
    assert ha.calls == 0


def test_inert_without_host_act():
    b = _bind(awake=True, host_act=None, reviewers=("r",), auto_review=True)
    b._maybe_auto_apply()                     # must not raise


def test_inert_when_flag_off():
    ha = _HA()
    b = _bind(awake=True, host_act=ha, reviewers=("r",), auto_review=False)
    b._maybe_auto_apply()
    assert ha.calls == 0


def test_armed_runs_apply():
    ha = _HA()
    b = _bind(awake=True, host_act=ha, reviewers=("r",), auto_review=True)
    b._maybe_auto_apply()
    assert ha.calls == 1                      # apply_verdicts -> host_act.pending


def test_call_tool_triggers_apply():
    ha = _HA()
    b = _bind(awake=True, host_act=ha, reviewers=("r",), auto_review=True)
    b.call_tool("conscio.advisory", {})       # any tool; advisory needs no host
    assert ha.calls == 1
