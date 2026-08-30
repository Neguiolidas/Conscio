"""Static checks on Observatory files (v4.5 design)."""
from pathlib import Path

_STATIC = Path(__file__).parent.parent / "conscio" / "observatory" / "static"


def test_static_files_exist():
    assert (_STATIC / "index.html").exists()
    assert (_STATIC / "app.js").exists()
    assert (_STATIC / "style.css").exists()
    assert (_STATIC / "d3.min.js").exists()
    assert (_STATIC / "graphview.js").exists()


def test_index_has_sidebar():
    html = (_STATIC / "index.html").read_text()
    assert "sidebar" in html                 # layout grid sidebar/topbar/main
    assert "nav-item" in html                # nav agrupada por seção
    assert "data-tab=" in html               # tabs com dispatcher
    assert "d3.min.js" in html
    assert "graphview.js" in html
    assert "app.js" in html


def test_app_has_state_and_polling():
    js = (_STATIC / "app.js").read_text()
    assert "state" in js                     # estado centralizado
    assert "setInterval" in js               # polling por visibilidade
    assert "paintTopbar" in js
    assert "relay-inbox" in js               # v4.5: inbox colapsável por peer
    assert "graphview" in js                 # graph via graphview.js (iframe)


def test_style_has_grafite_theme():
    css = (_STATIC / "style.css").read_text()
    assert "--bg:" in css                    # design tokens
    assert "--accent:" in css
    assert "--accent-border:" in css
    assert "relay-group" in css              # v4.5: collapsible relay styles