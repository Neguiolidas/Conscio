# tests/test_axis_pack_battery.py
"""v1.9 deep battery — axis-pack loader survives a binary pack file.

B-013 class (uniformity): _read_pack caught (json.JSONDecodeError, OSError) but a
binary/non-UTF-8 pack makes read_text raise UnicodeDecodeError (a ValueError, not
JSONDecodeError) → escaped. Loading axis packs is documented as advisory
("a missing pack is skipped, never fatal") — a corrupt pack must be skipped too.
"""
import conscio.axis_pack as ap


def test_try_break_binary_axis_pack_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "AXES_DIR", tmp_path)
    (tmp_path / "core.json").write_bytes(b"\xff\xfe\x00 not utf8 json \xff")
    assert ap._read_pack("core") == []           # skipped, not crashed
    assert ap.load_axes(["core"]) == []          # advisory load stays non-fatal


def test_try_keep_valid_axis_pack_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "AXES_DIR", tmp_path)
    (tmp_path / "core.json").write_text(
        '{"axes": [{"name": "up_down", "positive": ["up"], "negative": ["down"]}]}')
    assert ap._read_pack("core") == [
        {"name": "up_down", "positive": ["up"], "negative": ["down"]}]
