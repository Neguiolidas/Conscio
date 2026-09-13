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


def test_stop_words_and_short_anchors_are_rejected():
    """Emenda A6: âncora vaga/gramatical não pode atravessar como VERIFIED."""
    assert find_claims("rodei os testes") == []
    assert find_claims("rodei tudo de novo") == []
    assert find_claims("pushei tudo") == []
    assert find_claims("commitei tudo") == []
    pytest_claim = find_claims("rodei pytest")
    assert len(pytest_claim) == 1
    assert pytest_claim[0].cls_name == "test_run"
    assert pytest_claim[0].anchor == "pytest"

    push_claim = find_claims("pushei pra origin")
    assert len(push_claim) == 1
    assert push_claim[0].cls_name == "push"
    assert push_claim[0].anchor == "origin"

