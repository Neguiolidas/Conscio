# tests/test_plugins.py
"""Entry-point plugin discovery — resilient to broken/mistyped third-party plugins."""
import importlib.metadata as im
import warnings

from conscio.plugins import (
    discover_adapters,
    discover_sensors,
    discover_tools,
    load_entry_points,
)


class _EP:
    """Minimal EntryPoint double (name / group / load())."""

    def __init__(self, name, obj, group):
        self.name, self._obj, self.group = name, obj, group

    def load(self):
        return self._obj


class _BoomEP(_EP):
    def load(self):
        raise RuntimeError("broken plugin")


def _patch(monkeypatch, eps):
    class _Res(list):
        def select(self, group=None):
            return _Res(e for e in self if e.group == group)

    def fake_entry_points(*a, **k):
        res = _Res(eps)
        return res.select(group=k["group"]) if "group" in k else res

    monkeypatch.setattr(im, "entry_points", fake_entry_points)


def test_discovers_valid_adapter(monkeypatch):
    from conscio.agency.adapter import MockAdapter
    _patch(monkeypatch, [_EP("mock", MockAdapter, "conscio.adapters")])
    assert discover_adapters()["mock"] is MockAdapter


def test_discovers_valid_sensor(monkeypatch):
    from conscio.perception import MockSensor
    _patch(monkeypatch, [_EP("mock", MockSensor, "conscio.sensors")])
    assert discover_sensors()["mock"] is MockSensor


def test_discovers_callable_tool(monkeypatch):
    def make_tool():
        return "tool"
    _patch(monkeypatch, [_EP("t", make_tool, "conscio.tools")])
    assert discover_tools()["t"] is make_tool


def test_wrong_type_adapter_is_skipped(monkeypatch):
    _patch(monkeypatch, [_EP("bad", int, "conscio.adapters")])
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert discover_adapters() == {}        # int is not an InferenceAdapter
    assert any("bad" in str(x.message) for x in w)


def test_load_failure_is_skipped_with_warning(monkeypatch):
    _patch(monkeypatch, [_BoomEP("x", None, "conscio.sensors")])
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert discover_sensors() == {}
    assert any("broken plugin" in str(x.message) or "x" in str(x.message)
               for x in w)


def test_one_bad_plugin_does_not_hide_good_one(monkeypatch):
    from conscio.agency.adapter import MockAdapter
    _patch(monkeypatch, [_BoomEP("bad", None, "conscio.adapters"),
                         _EP("good", MockAdapter, "conscio.adapters")])
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        found = discover_adapters()
    assert found == {"good": MockAdapter}       # resilience


def test_unknown_group_is_empty(monkeypatch):
    _patch(monkeypatch, [])
    assert load_entry_points("conscio.nope") == {}
