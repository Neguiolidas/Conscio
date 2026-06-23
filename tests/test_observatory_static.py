# tests/test_observatory_static.py
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[1] / "conscio" / "observatory" / "static"


def test_static_trio_exists_and_nonempty():
    for name in ("index.html", "app.js", "style.css"):
        fp = _STATIC / name
        assert fp.is_file() and fp.stat().st_size > 0, name


def test_app_js_targets_readonly_api_and_no_external_fetch():
    js = (_STATIC / "app.js").read_text()
    for ep in ("/api/events", "/api/goals", "/api/actions", "/api/skills", "/api/state"):
        assert ep in js, ep
    assert "http://" not in js and "https://" not in js   # same-origin only


def test_index_flags_persisted_freshness():
    html = (_STATIC / "index.html").read_text().lower()
    assert "last-persisted" in html or "may lag" in html
