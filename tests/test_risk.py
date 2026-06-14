# tests/test_risk.py
"""The Risk enum has one canonical home (conscio.risk); agency re-exports it."""


def test_canonical_risk_has_three_tiers():
    from conscio.risk import Risk
    assert {r.name for r in Risk} == {"LOW", "MEDIUM", "HIGH"}
    assert Risk.LOW.value == "low"
    assert Risk.MEDIUM.value == "medium"
    assert Risk.HIGH.value == "high"


def test_agency_tools_reexports_same_object():
    from conscio.agency.tools import Risk as ViaTools
    from conscio.risk import Risk as Canonical
    assert ViaTools is Canonical          # identity, not a copy


def test_agency_package_reexports_same_object():
    from conscio.agency import Risk as ViaPackage
    from conscio.risk import Risk as Canonical
    assert ViaPackage is Canonical


def test_risk_json_serializes_to_stable_wire_value():
    import json

    from conscio.risk import Risk
    # str-Enum: the wire value is the lowercase string, stable across versions.
    assert json.dumps({"risk": Risk.HIGH}) == '{"risk": "high"}'
    assert Risk.LOW == "low"                       # equality with the raw value


def test_risk_round_trips_from_wire_value():
    import json

    from conscio.risk import Risk
    restored = Risk(json.loads('"medium"'))
    assert restored is Risk.MEDIUM
