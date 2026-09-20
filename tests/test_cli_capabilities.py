"""v4.6.5: um caminho de primeira classe para conceder e revogar capacidade.

Sem ele, o aviso do SessionStart nao teria remedio a oferecer: hoje a unica
forma de conceder relay/halls e o wizard ou `conscio init --repair`, e nenhum
dos dois roda no caminho do marketplace -- que foi exatamente como a 4.6.4
desarmou o relay em silencio.
"""
from conscio.cli import main
from conscio.mcp import capabilities as caps


def test_list_shows_nothing_granted_without_pretending_it_is_an_error(
        tmp_path, capsys):
    """Saida vazia seria indistinguivel de comando quebrado."""
    assert main(["capabilities", "--storage", str(tmp_path)]) == 0
    assert "none" in capsys.readouterr().out.lower()


def test_enable_persists_the_capability_in_the_space(tmp_path, capsys):
    assert main(["capabilities", "enable", "relay",
                 "--storage", str(tmp_path)]) == 0
    assert caps.read_capabilities(tmp_path) == {"relay"}


def test_list_shows_what_was_granted(tmp_path, capsys):
    main(["capabilities", "enable", "relay", "--storage", str(tmp_path)])
    assert main(["capabilities", "--storage", str(tmp_path)]) == 0
    assert "relay" in capsys.readouterr().out


def test_disable_revokes_it(tmp_path):
    """Revogacao tem de funcionar, senao o aviso vira mao unica: o usuario
    consegue ligar e nunca desligar."""
    main(["capabilities", "enable", "relay", "--storage", str(tmp_path)])
    main(["capabilities", "enable", "halls", "--storage", str(tmp_path)])
    assert main(["capabilities", "disable", "relay",
                 "--storage", str(tmp_path)]) == 0
    assert caps.read_capabilities(tmp_path) == {"halls"}


def test_an_unknown_capability_fails_loudly(tmp_path, capsys):
    """Nome errado nao pode sair 0 e nao fazer nada -- seria a mesma falha
    silenciosa que esta versao existe para matar."""
    assert main(["capabilities", "enable", "telepatia",
                 "--storage", str(tmp_path)]) != 0
    assert caps.read_capabilities(tmp_path) == set()


def test_enable_without_a_name_says_how_to_use_it(tmp_path, capsys):
    """Ramo que o self-review achou sem cobertura. Escrito DEPOIS do codigo --
    entao provado por mutacao, nao por ter visto vermelho antes."""
    assert main(["capabilities", "enable", "--storage", str(tmp_path)]) == 2
    assert "usage" in capsys.readouterr().out.lower()
