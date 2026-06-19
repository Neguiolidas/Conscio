# tests/test_durable_guards.py
"""v1.9 durable guards — stop the recurring bug CLASSES (tz, narrow-except,
sentinel-as-unbounded) from resurfacing in NEW modules. The Wave-1/2 fixes solved
'now'; these guard 'next' (Hermet's systemic-risk note).
"""
import ast
import pathlib

from conscio.guards import clamp_int, read_json_dict, safe_read_json

_CONSCIO = pathlib.Path(__file__).resolve().parent.parent / "conscio"


# ── tz class: ban bare datetime.fromtimestamp() outside timeutil ──────────────
def test_no_bare_fromtimestamp_outside_timeutil():
    """B-003b / B-007 class: datetime.fromtimestamp(ts) is NAIVE LOCAL and skews any
    comparison against the naive-UTC event store. The ONLY sanctioned converter is
    timeutil.naive_utc_from_epoch (which passes timezone.utc). A bare call in any
    other module is a regression — fail CI here so v1.10/v1.11 can't reintroduce it.

    AST-based (a real linter rule): inspects Call nodes, so docstrings/comments that
    merely mention fromtimestamp don't trip it. A call is OK only with a tz: a 2nd
    positional arg or a tz= keyword.
    """
    offenders = []
    for py in _CONSCIO.rglob("*.py"):
        if py.name == "timeutil.py":
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "fromtimestamp"):
                has_tz = (len(node.args) >= 2
                          or any(kw.arg == "tz" for kw in node.keywords))
                if not has_tz:
                    offenders.append(
                        f"{py.relative_to(_CONSCIO.parent)}:{node.lineno}")
    assert not offenders, (
        "bare datetime.fromtimestamp() (naive local) found — use "
        "timeutil.naive_utc_from_epoch:\n" + "\n".join(offenders))


# ── narrow-except / corrupt-read class: safe_read_json never raises ───────────
class TestSafeReadJson:
    def test_missing_file_is_none(self, tmp_path):
        assert safe_read_json(tmp_path / "nope.json") is None

    def test_try_break_binary_file_is_none(self, tmp_path):
        p = tmp_path / "b.json"
        p.write_bytes(b"\xff\xfe\x00 not utf8")
        assert safe_read_json(p) is None

    def test_try_break_malformed_json_is_none(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text("{ not json")
        assert safe_read_json(p) is None

    def test_try_break_non_dict_json_is_none(self, tmp_path):
        p = tmp_path / "l.json"
        p.write_text("[1, 2, 3]")
        assert safe_read_json(p) is None

    def test_try_keep_valid_dict(self, tmp_path):
        p = tmp_path / "d.json"
        p.write_text('{"a": 1}')
        assert safe_read_json(p) == {"a": 1}


# ── schema-drift / incomplete-JSON class: read_json_dict ─────────────────────
class TestReadJsonDict:
    _DEFAULT: dict[str, object] = {"entities": {}, "relations": [], "predictions": []}

    def test_try_break_incomplete_fills_missing_keys(self, tmp_path):
        p = tmp_path / "w.json"
        p.write_text('{"entities": {"bot": {"type": "system"}}}')
        out = read_json_dict(p, dict(self._DEFAULT))
        assert out["entities"] == {"bot": {"type": "system"}}   # loaded wins
        assert out["relations"] == [] and out["predictions"] == []  # filled

    def test_try_break_empty_dict_gets_full_skeleton(self, tmp_path):
        p = tmp_path / "w.json"
        p.write_text("{}")
        assert read_json_dict(p, dict(self._DEFAULT)) == self._DEFAULT

    def test_try_break_missing_file_is_default(self, tmp_path):
        assert read_json_dict(tmp_path / "nope.json",
                              dict(self._DEFAULT)) == self._DEFAULT

    def test_try_break_corrupt_is_default(self, tmp_path):
        p = tmp_path / "w.json"
        p.write_bytes(b"\xff\xfe not json")
        assert read_json_dict(p, dict(self._DEFAULT)) == self._DEFAULT

    def test_does_not_mutate_caller_default(self, tmp_path):
        p = tmp_path / "w.json"
        p.write_text('{"entities": {"x": 1}}')
        default = dict(self._DEFAULT)
        read_json_dict(p, default)
        assert default["entities"] == {}            # caller default untouched


# ── sentinel-as-unbounded class: clamp_int ───────────────────────────────────
class TestClampInt:
    def test_try_break_below(self):
        assert clamp_int(-1, 0, 100) == 0

    def test_try_break_above(self):
        assert clamp_int(10 ** 9, 0, 100) == 100

    def test_try_keep_in_range(self):
        assert clamp_int(42, 0, 100) == 42

    def test_boundaries_inclusive(self):
        assert clamp_int(0, 0, 100) == 0
        assert clamp_int(100, 0, 100) == 100
