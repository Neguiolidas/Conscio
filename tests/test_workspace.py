"""v1.5 WorkspaceContext — environment & workspace awareness (component E).

Detects WHERE Conscio is running: the active workspace root (explicit ->
CONSCIO_WORKSPACE -> git-root walk -> cwd) and the environment class (STABLE for
IDE/CLI, SWITCHING for workspace-hopping agents, UNKNOWN otherwise). Emits
workspace:changed when the root id changes. v1.5 builds only detection + the
change signal — the per-workspace scope/consent flow is v1.6.
"""
from conscio.workspace import EnvClass, Workspace, WorkspaceContext


def test_explicit_root_wins_and_id_is_stable(tmp_path):
    ctx = WorkspaceContext(explicit_root=tmp_path, environ={})
    ws = ctx.current()
    assert isinstance(ws, Workspace)
    assert ws.root == tmp_path.resolve()
    assert ws.id == ctx.current().id            # deterministic for same root


def test_env_var_root_used_when_no_explicit(tmp_path):
    ctx = WorkspaceContext(environ={"CONSCIO_WORKSPACE": str(tmp_path)})
    assert ctx.current().root == tmp_path.resolve()


def test_git_root_walk(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    sub = proj / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    ctx = WorkspaceContext(environ={})           # no explicit, no env -> git walk
    assert ctx.current().root == proj.resolve()


def test_cwd_fallback_when_no_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = WorkspaceContext(environ={})
    assert ctx.current().root == tmp_path.resolve()


def test_distinct_roots_have_distinct_ids(tmp_path):
    a = WorkspaceContext(explicit_root=tmp_path / "a", environ={}).current()
    b = WorkspaceContext(explicit_root=tmp_path / "b", environ={}).current()
    assert a.id != b.id


class TestEnvClass:
    def test_explicit_config_stable(self, tmp_path):
        ctx = WorkspaceContext(explicit_root=tmp_path,
                               environ={"CONSCIO_ENV_CLASS": "stable"})
        assert ctx.current().env is EnvClass.STABLE

    def test_explicit_config_switching(self, tmp_path):
        ctx = WorkspaceContext(explicit_root=tmp_path,
                               environ={"CONSCIO_ENV_CLASS": "switching"})
        assert ctx.current().env is EnvClass.SWITCHING

    def test_ide_hint_is_stable(self, tmp_path):
        ctx = WorkspaceContext(explicit_root=tmp_path,
                               environ={"TERM_PROGRAM": "vscode"})
        assert ctx.current().env is EnvClass.STABLE

    def test_agent_hint_is_switching(self, tmp_path):
        ctx = WorkspaceContext(explicit_root=tmp_path,
                               environ={"OPENCLAW_SESSION": "1"})
        assert ctx.current().env is EnvClass.SWITCHING

    def test_default_is_unknown(self, tmp_path):
        ctx = WorkspaceContext(explicit_root=tmp_path, environ={})
        assert ctx.current().env is EnvClass.UNKNOWN

    def test_unknown_and_switching_recheck_each_cycle(self, tmp_path):
        unknown = Workspace(tmp_path, EnvClass.UNKNOWN, "x")
        switching = Workspace(tmp_path, EnvClass.SWITCHING, "x")
        stable = Workspace(tmp_path, EnvClass.STABLE, "x")
        assert unknown.recheck_each_cycle is True       # safer assumption
        assert switching.recheck_each_cycle is True
        assert stable.recheck_each_cycle is False


class TestPollChangeSignal:
    def test_poll_emits_on_root_change(self, tmp_path, monkeypatch):
        events = []

        def emit(**kw):
            events.append(kw)

        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        monkeypatch.chdir(a)
        ctx = WorkspaceContext(environ={}, emit=emit)
        assert events == []                              # no event on construct
        monkeypatch.chdir(b)
        ws = ctx.poll()
        assert ws.root == b.resolve()
        assert len(events) == 1
        assert events[0]["type"] == "workspace:changed"
        assert events[0]["data"]["to"] == ws.id

    def test_poll_silent_when_root_unchanged(self, tmp_path, monkeypatch):
        events = []
        monkeypatch.chdir(tmp_path)
        ctx = WorkspaceContext(environ={}, emit=lambda **kw: events.append(kw))
        ctx.poll()
        ctx.poll()
        assert events == []
