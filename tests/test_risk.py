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
