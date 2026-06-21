# tests/test_hub_model_test.py
from conscio.agency.adapter import AdapterTimeout
from conscio.hub import model_test


def test_smoke_ok(monkeypatch):
    from conscio.agency import MockAdapter
    monkeypatch.setattr(model_test, "_build",
                        lambda pc, model: MockAdapter(script=["OK"]))
    out = model_test.smoke_test({"type": "openai"}, "m")
    assert out["ok"] is True and out["sample_output"] == "OK"


def test_smoke_adapter_error(monkeypatch):
    class Boom:
        def generate(self, *a, **k):
            raise AdapterTimeout("slow")
    monkeypatch.setattr(model_test, "_build", lambda pc, model: Boom())
    out = model_test.smoke_test({"type": "openai"}, "m")
    assert out["ok"] is False and "slow" in out["error"]


def test_smoke_no_adapter(monkeypatch):
    monkeypatch.setattr(model_test, "_build", lambda pc, model: None)
    out = model_test.smoke_test({"type": "bogus"}, "m")
    assert out["ok"] is False
