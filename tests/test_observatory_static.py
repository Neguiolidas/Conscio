"""Static checks on Observatory files."""
from pathlib import Path

_STATIC = Path(__file__).parent.parent / "conscio" / "observatory" / "static"


def test_static_files_exist():
    assert (_STATIC / "index.html").exists()
    assert (_STATIC / "app.js").exists()
    assert (_STATIC / "style.css").exists()
    assert (_STATIC / "d3.min.js").exists()


def test_index_has_sidebar():
    html = (_STATIC / "index.html").read_text()
    assert "sidebar" in html
    assert "hamburger" in html
    assert "project-list" in html
    assert "d3.min.js" in html


def test_app_has_graph_renderer():
    js = (_STATIC / "app.js").read_text()
    assert "App._renderGraph" in js
    assert "forceSimulation" in js
    assert "getContext" in js  # Canvas
    assert "graph-canvas" in js