import logging
from pathlib import Path

from conscio.installer import binding


def test_none_storage_is_ok():
    assert binding.validate_binding(None) is True


def test_missing_dir_is_bootstrapped_silently(tmp_path, caplog):
    target = tmp_path / "fresh-space"
    with caplog.at_level(logging.WARNING):
        ok = binding.validate_binding(str(target))
    assert ok is True
    assert (target / "instance.json").exists()
    assert "conscio init --repair" not in caplog.text


def test_tilde_storage_is_expanded_not_created_literally(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)           # a literal `~` would land here
    ok = binding.validate_binding("~/tilde-space")
    assert ok is True
    assert (tmp_path / "tilde-space" / "instance.json").exists()
    assert not (Path.cwd() / "~").exists()


def test_empty_dir_is_bootstrapped_not_called_drift(tmp_path, caplog):
    """Container mounts and `mkdir -p` hand us an existing empty directory.
    That is a fresh space, not drift: bootstrap it and stay quiet, otherwise
    every startup nags and the space never gets an identity."""
    sp = tmp_path / "space"
    sp.mkdir()
    with caplog.at_level(logging.WARNING):
        ok = binding.validate_binding(sp)
    assert ok is True
    assert (sp / "instance.json").exists()
    assert caplog.text == ""


def test_populated_dir_without_identity_warns(tmp_path, caplog):
    """Real drift: contents but no identity, or a --storage typo landing on an
    unrelated directory. Must still warn and must not adopt the directory."""
    sp = tmp_path / "space"
    sp.mkdir()
    (sp / "conscio.db").write_text("not empty")
    with caplog.at_level(logging.WARNING):
        ok = binding.validate_binding(sp)
    assert ok is False
    assert "drift" in caplog.text
    assert not (sp / "instance.json").exists()


def test_healthy_binding_ok(tmp_path):
    sp = tmp_path / "space"
    sp.mkdir()
    (sp / "instance.json").write_text("{}")
    assert binding.validate_binding(sp) is True


def test_never_raises_on_garbage():
    assert binding.validate_binding(12345) is True   # unusable -> treated as ok


# --- v4.7.2: a host that never substituted the plugin variable -------------

def test_unexpanded_variable_names_the_culprit():
    uv = binding.unexpanded_variable
    assert uv("${CLAUDE_PLUGIN_DATA}/space") == "CLAUDE_PLUGIN_DATA"
    assert uv("/x/${CLAUDE_PLUGIN_DATA}/space") == "CLAUDE_PLUGIN_DATA"
    assert uv("${DATA:-/tmp/d}/space") == "DATA"
    assert uv("$HOME/.conscio") == "HOME"


def test_real_paths_have_no_unexpanded_variable():
    uv = binding.unexpanded_variable
    for ok in (None, "", "~/.conscio/instances/gepeto", "/srv/a$/b",
               "/home/u/.zcode/cli/plugins/data/conscio@conscio/space",
               "C:\\Users\\u\\space", 42):
        assert uv(ok) is None, ok


def test_mcp_refuses_literal_variable_and_creates_nothing(
        tmp_path, monkeypatch, capsys):
    # The Codex case: the plugin's .mcp.json run verbatim, cwd = a repo.
    from conscio.mcp import server
    monkeypatch.chdir(tmp_path)
    rc = server.main(["--storage", "${CLAUDE_PLUGIN_DATA}/space",
                      "--model", "mock"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "CLAUDE_PLUGIN_DATA" in err and "absolute --storage" in err
    assert list(tmp_path.iterdir()) == [], "a phantom space was created"
