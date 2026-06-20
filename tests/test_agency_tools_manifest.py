# tests/test_agency_tools_manifest.py
import pytest
from conscio.agency.tools import registry_from_manifest
from conscio.risk import Risk


def _m(**kw):
    base = {"name": "deploy", "description": "d",
            "params": {"env": {"type": "str", "required": True}},
            "risk": "high", "approval_policy": "require_approval"}
    base.update(kw)
    return [base]


def test_manifest_builds_registry_with_risk_and_policy():
    reg = registry_from_manifest(_m())
    spec = reg.get("deploy")
    assert spec.risk is Risk.HIGH
    assert spec.approval_policy == "require_approval"
    assert spec.params == {"env": {"type": "str", "required": True}}


def test_missing_risk_defaults_high_and_policy_defaults_require_approval():
    reg = registry_from_manifest([{"name": "x", "params": {}}])
    spec = reg.get("x")
    assert spec.risk is Risk.HIGH
    assert spec.approval_policy == "require_approval"


def test_sentinel_fn_raises_if_dispatched():
    reg = registry_from_manifest(_m(risk="low", approval_policy="auto"))
    res = reg.dispatch("deploy", {"env": "prod"})
    assert res.ok is False and "host" in res.error.lower()


@pytest.mark.parametrize("bad", [
    "not-a-list",
    [{"params": {}}],                       # no name
    [{"name": "x", "risk": "boom"}],        # bad risk
    [{"name": "x", "approval_policy": "nope"}],  # bad policy
    [{"name": "x", "params": "notdict"}],   # bad params
])
def test_invalid_manifest_raises_valueerror(bad):
    with pytest.raises(ValueError):
        registry_from_manifest(bad)
