"""TDD: KnowledgeGraph — entities + triples em SQLite."""
from conscio.kg import KnowledgeGraph


def test_kg_add_entity(tmp_path):
    kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
    eid = kg.add_entity("grolv.com.br", entity_type="domain")
    assert eid == "grolv.com.br"
    ent = kg.query_entity("grolv.com.br")
    assert ent is not None
    assert ent["name"] == "grolv.com.br"
    assert ent["type"] == "domain"
    kg.close()


def test_kg_add_triple(tmp_path):
    kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
    kg.add_entity("samuel", entity_type="person")
    kg.add_entity("grolv.com.br", entity_type="domain")
    tid = kg.add_triple("samuel", "owns", "grolv.com.br")
    assert tid is not None
    rels = kg.query_relationship("samuel")
    assert len(rels) == 1
    assert rels[0]["predicate"] == "owns"
    assert rels[0]["object"] == "grolv.com.br"
    kg.close()


def test_kg_timeline(tmp_path):
    kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
    kg.add_entity("conscio", entity_type="project")
    kg.add_entity("v3.1.0", entity_type="version")
    kg.add_triple("conscio", "released", "v3.1.0", valid_from="2026-07-21")
    tl = kg.timeline("conscio")
    assert len(tl) == 1
    assert tl[0]["predicate"] == "released"
    kg.close()


def test_kg_stats(tmp_path):
    kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
    kg.add_entity("a", entity_type="x")
    kg.add_entity("b", entity_type="y")
    kg.add_triple("a", "rel", "b")
    s = kg.stats()
    assert s["entities"] == 2
    assert s["triples"] == 1
    kg.close()


def test_kg_persistence(tmp_path):
    db = tmp_path / "kg.db"
    kg = KnowledgeGraph(db_path=db)
    kg.add_entity("test", entity_type="t")
    kg.close()
    kg2 = KnowledgeGraph(db_path=db)
    ent = kg2.query_entity("test")
    assert ent is not None
    assert ent["name"] == "test"
    kg2.close()
