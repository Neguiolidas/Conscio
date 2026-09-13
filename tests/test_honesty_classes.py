# tests/test_honesty_classes.py
from conscio.honesty.classes import CLASSES, find_claims


def test_commit_claim_with_anchor_is_found():
    claims = find_claims("commitei em abc1234 e segui")
    assert [c.cls_name for c in claims] == ["commit"]
    assert claims[0].anchor == "abc1234"


def test_english_is_covered_too():
    claims = find_claims("committed abc1234")
    assert [c.cls_name for c in claims] == ["commit"]


def test_claim_without_anchor_is_dropped():
    """Sem artefato nomeado não há predicado possível."""
    assert find_claims("commitei tudo") == []


def test_vague_verification_is_never_a_class():
    assert find_claims("verifiquei e confirmei que está certo") == []


def test_every_class_is_bilingual():
    """Classe que exista num idioma só falha o build, não o silêncio."""
    for cls in CLASSES:
        assert cls.patterns_pt, f"{cls.name} sem padrão pt"
        assert cls.patterns_en, f"{cls.name} sem padrão en"
