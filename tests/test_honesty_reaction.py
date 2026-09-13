# tests/test_honesty_reaction.py
from conscio.agency import outcome as o
from conscio.honesty.reaction import attribute, read_reaction


def test_direct_failure_report_is_contradicted():
    assert read_reaction("não funcionou, ainda dá erro") == o.CONTRADICTED


def test_approval_never_verifies():
    """'ok' atesta satisfação, não que o artefato mudou (N2)."""
    assert read_reaction("ok, perfeito") is None


def test_ambiguous_text_yields_nothing():
    assert read_reaction("faz de novo o próximo") is None


def test_attribution_requires_exactly_one_pending():
    assert attribute([{"id": 7}]) == 7
    assert attribute([{"id": 7}, {"id": 8}]) is None
    assert attribute([]) is None
