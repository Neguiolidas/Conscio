"""TDD: EntityDetector — simplified entity detection."""
from conscio.entity_detector import EntityDetector


def test_detect_person():
    ed = EntityDetector()
    ents = ed.detect("Samuel asked Conscio to reflect on the output. Samuel owns grolv.com.br.")
    names = {e["name"] for e in ents}
    assert "Samuel" in names


def test_detect_domain():
    ed = EntityDetector()
    ents = ed.detect("Deploy vault.grolv.com.br with Firebase auth and check firebase.auth()")
    names = {e["name"] for e in ents}
    assert "vault.grolv.com.br" in names
    assert "Firebase" in names


def test_detect_project():
    ed = EntityDetector()
    ents = ed.detect("Conscio v3.1.0 was released. Hermes gateway is running.")
    names = {e["name"] for e in ents}
    assert "Conscio" in names
    assert "Hermes" in names


def test_detect_to_kg(tmp_path):
    """Integrates with KnowledgeGraph — adds entities (no inferred relations)."""
    from conscio.kg import KnowledgeGraph
    kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
    ed = EntityDetector(kg=kg)
    ed.detect_and_store("Samuel owns grolv.com.br. Samuel released Conscio v3.1.0.")
    ent = kg.query_entity("Samuel")
    assert ent is not None
    ent2 = kg.query_entity("grolv.com.br")
    assert ent2 is not None
    kg.close()


def test_detect_empty():
    ed = EntityDetector()
    assert ed.detect("just a regular sentence about things in general") == []


def test_detect_unicode_portuguese():
    """Português acentuado."""
    ed = EntityDetector()
    ents = ed.detect("São Paulo é a maior cidade. João trabalha com Conscio.")
    names = {e["name"] for e in ents}
    assert "São" in names
    assert "João" in names
    assert "Conscio" in names


def test_detect_version():
    ed = EntityDetector()
    ents = ed.detect("Upgraded to v3.1.0 of the framework")
    names = {e["name"] for e in ents}
    assert "v3.1.0" in names or any("3.1.0" in n for n in names)


def test_detect_lowercase_start_not_entity():
    """Word starting lowercase verbo should not be detected as entity."""
    ed = EntityDetector()
    ents = ed.detect("the quick brown fox jumps over")
    assert all(e["name"] not in {"the", "quick", "brown", "fox", "jumps", "over"} for e in ents)
