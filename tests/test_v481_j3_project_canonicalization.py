import pytest

from conscio import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


@pytest.fixture
def repo_env(tmp_path):
    # Repo 1
    repo = tmp_path / "my_project"
    repo.mkdir()
    (repo / ".git").mkdir()
    subdir = repo / "src" / "pkg"
    subdir.mkdir(parents=True)

    # Repo in home
    home = tmp_path / "home"
    home.mkdir()
    home_repo = home / "home_project"
    home_repo.mkdir()
    (home_repo / ".git").mkdir()

    storage = tmp_path / "storage"
    storage.mkdir()

    engine = ConsciousnessEngine(model_name="mock", storage_path=storage, delivery_check=False)
    server = Bindings(engine, SeenStore(":memory:"))

    # Writers store canonicalized project_root
    engine.observe(
        tool="test_runner",
        input_text="pytest",
        output_text="test_output_unique_token_repo",
        project=str(repo.resolve()),
        session_id="session-repo-1",
    )
    engine.observe(
        tool="test_runner",
        input_text="pytest",
        output_text="test_output_unique_token_home",
        project=str(home_repo.resolve()),
        session_id="session-home-1",
    )

    yield {
        "server": server,
        "engine": engine,
        "repo": repo,
        "subdir": subdir,
        "home": home,
        "home_repo": home_repo,
    }
    engine.close()


def test_recall_observations_canonicalizes_project_path(repo_env, monkeypatch):
    server = repo_env["server"]
    repo = repo_env["repo"]
    subdir = repo_env["subdir"]
    home = repo_env["home"]

    # (a) Subdiretório (scope=project)
    res_sub = server._recall_observations(
        {
            "query": "unique_token_repo",
            "scope": "project",
            "project": str(subdir),
        }
    )
    assert len(res_sub["observations"]) == 1, (
        f"Failed to find observation when project is subdirectory: {res_sub}"
    )

    # (b) Barra no fim (scope=project)
    res_slash = server._recall_observations(
        {
            "query": "unique_token_repo",
            "scope": "project",
            "project": str(repo) + "/",
        }
    )
    assert len(res_slash["observations"]) == 1, (
        f"Failed to find observation when project has trailing slash: {res_slash}"
    )

    # (c) Caminho com ~ via monkeypatch de HOME (scope=project)
    monkeypatch.setenv("HOME", str(home))
    res_tilde = server._recall_observations(
        {
            "query": "unique_token_home",
            "scope": "project",
            "project": "~/home_project",
        }
    )
    assert len(res_tilde["observations"]) == 1, (
        f"Failed to find observation when project uses ~: {res_tilde}"
    )

    # (d) Subdiretório com scope=session (resolução de target_project)
    res_sess = server._recall_observations(
        {
            "query": "unique_token_repo",
            "scope": "session",
            "project": str(subdir),
        }
    )
    assert len(res_sess["observations"]) == 1
    assert res_sess["session_id"] == "session-repo-1"
