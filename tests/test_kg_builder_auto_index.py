"""Tests for kg_builder and auto_index modules (v3.3.1)."""


import pytest

from conscio.content_store import ContentStore
from conscio.kg import KnowledgeGraph
from conscio.kg_builder import KGBuilder, extract_entities, build_triples, ExtractedEntity


# ── extract_entities ───────────────────────────────────────────────────

class TestExtractEntities:
    def test_url_extraction(self):
        text = "Deployed at https://api.example.com/v2/models"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert "https://api.example.com/v2/models" in names

    def test_ip_extraction(self):
        text = "Server running on 192.168.1.100:8080"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert "192.168.1.100:8080" in names

    def test_email_extraction(self):
        text = "Contact admin@conscio.dev for support"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert "admin@conscio.dev" in names

    def test_tech_entity_extraction(self):
        text = "Running Conscio with Docker and SQLite for persistence"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert "Conscio" in names
        assert "Docker" in names
        assert "SQLite" in names

    def test_version_extraction(self):
        text = "Upgraded to v3.3.1 of the framework"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert "v3.3.1" in names

    def test_filepath_extraction(self):
        text = "Config file at /home/ubuntu/.config/conscio/config.json"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert any("/home/ubuntu" in n for n in names)

    def test_capitalized_concept(self):
        text = "The Knowledge Graph Builder extracts entities from ContentStore"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        # Regex catches "Knowledge Graph Builder" or "Knowledge Graph" or similar
        assert any("Knowledge Graph" in n for n in names)

    def test_no_short_proper_nouns(self):
        text = "I went to the store yesterday"
        ents = extract_entities(text)
        # "I" is too short, should not be a concept
        assert not any(e.entity_type == "concept" and len(e.name) < 5 for e in ents)

    def test_empty_text(self):
        ents = extract_entities("")
        assert ents == []

    def test_identifier_extraction(self):
        text = "Called build_adapter_from_config() to load settings"
        ents = extract_entities(text)
        names = [e.name for e in ents]
        assert "build_adapter_from_config" in names

    def test_duplicate_dedup(self):
        text = "Using Docker v3.3.1 with Docker again"
        ents = extract_entities(text)
        # "Docker" should appear only once
        docker_count = sum(1 for e in ents if e.name == "Docker")
        assert docker_count == 1


# ── build_triples ─────────────────────────────────────────────────────

class TestBuildTriples:
    def test_mentioned_in_triple(self):
        ents = [ExtractedEntity("Docker", "technology"), ExtractedEntity("3.3.1", "version")]
        triples = build_triples(ents, source_id=42)
        assert any(t[1] == "mentioned_in" and t[2] == "source:42" for t in triples)

    def test_co_occurs_triple(self):
        ents = [ExtractedEntity("Docker", "technology"), ExtractedEntity("SQLite", "technology")]
        triples = build_triples(ents, source_id=1)
        assert any(t[1] == "co_occurs_with" for t in triples)
        # Both directions
        assert any(t[0] == "Docker" and t[2] == "SQLite" for t in triples)
        assert any(t[0] == "SQLite" and t[2] == "Docker" for t in triples)

    def test_single_entity(self):
        ents = [ExtractedEntity("Docker", "technology")]
        triples = build_triples(ents, source_id=1)
        # Only mentioned_in, no co_occurs
        assert len(triples) == 1
        assert triples[0][1] == "mentioned_in"


# ── KGBuilder integration ────────────────────────────────────────────

class TestKGBuilder:
    @pytest.fixture
    def stores(self, tmp_path):
        cs = ContentStore(db_path=tmp_path / "content_store.db")
        kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
        yield cs, kg
        cs.close()
        kg.close()

    def test_incremental_run(self, stores):
        cs, kg = stores
        # Index some content
        cs.index("test_1", "Conscio v3.3.1 deployed with Docker at https://conscio.dev", "external")
        cs.index("test_2", "Running on 192.168.1.100 with SQLite persistence", "external")
        cs.index("test_3", "Contact admin@orion.dev for support with Python 3.12", "external")

        builder = KGBuilder(cs, kg)
        result = builder.run()
        assert result["sources_scanned"] == 3
        assert result["entities_added"] > 0
        assert result["triples_added"] > 0

    def test_checkpoint(self, stores):
        cs, kg = stores
        cs.index("test_1", "Conscio v3.3.1 deployed", "external")
        builder = KGBuilder(cs, kg)

        # First run processes source 1
        r1 = builder.run()
        assert r1["sources_scanned"] == 1

        # Second run should find nothing new
        r2 = builder.run()
        assert r2["sources_scanned"] == 0

        # Add new source and run again
        cs.index("test_2", "Docker containers running PostgreSQL", "external")
        r3 = builder.run()
        assert r3["sources_scanned"] == 1

    def test_limit_respected(self, stores):
        cs, kg = stores
        for i in range(10):
            cs.index(f"test_{i}", f"Content about Conscio iteration {i}", "external")

        builder = KGBuilder(cs, kg)
        r1 = builder.run(limit=3)
        assert r1["sources_scanned"] == 3

        r2 = builder.run(limit=3)
        assert r2["sources_scanned"] == 3

    def test_empty_store(self, stores):
        cs, kg = stores
        builder = KGBuilder(cs, kg)
        result = builder.run()
        assert result["entities_added"] == 0
        assert result["triples_added"] == 0
        assert result["sources_scanned"] == 0

    def test_entities_queryable(self, stores):
        cs, kg = stores
        cs.index("test_1", "Conscio deployed with Docker and SQLite", "external")
        builder = KGBuilder(cs, kg)
        builder.run()

        # Query the KG for Docker
        docker = kg.query_entity("Docker")
        assert docker is not None
        assert docker["type"] == "technology"

    def test_triples_queryable(self, stores):
        cs, kg = stores
        cs.index("test_1", "Conscio deployed with Docker and SQLite", "external")
        builder = KGBuilder(cs, kg)
        builder.run()

        # Query relationships for Conscio
        rels = kg.query_relationship("Conscio")
        assert len(rels) > 0


# ── AutoIndexer ───────────────────────────────────────────────────────

class TestAutoIndexer:
    @pytest.fixture
    def engine_with_cs(self, tmp_path):
        from conscio.engine import ConsciousnessEngine
        eng = ConsciousnessEngine(model_name="test", storage_path=str(tmp_path))
        # Ensure content_store exists
        from conscio.content_store import ContentStore
        eng.content_store = ContentStore(db_path=tmp_path / "content_store.db")
        yield eng
        if hasattr(eng, '_auto_indexer') and eng._auto_indexer and eng._auto_indexer._installed:
            eng._auto_indexer.uninstall()

    def test_install_uninstall(self, engine_with_cs):
        from conscio.auto_index import AutoIndexer
        indexer = AutoIndexer(engine_with_cs)

        indexer.install()
        assert indexer._installed is True
        assert indexer._original_reflect is not None

        indexer.uninstall()
        assert indexer._installed is False

    def test_double_install_noop(self, engine_with_cs):
        from conscio.auto_index import AutoIndexer
        indexer = AutoIndexer(engine_with_cs)
        indexer.install()
        indexer.install()  # should be no-op
        assert indexer._installed is True
        indexer.uninstall()

    def test_enable_auto_index_engine_method(self, tmp_path):
        from conscio.engine import ConsciousnessEngine
        eng = ConsciousnessEngine(model_name="test", storage_path=str(tmp_path))
        # Index some initial content for KG builder to process
        from conscio.content_store import ContentStore
        cs = ContentStore(db_path=tmp_path / "content_store.db")
        cs.index("test_1", "Conscio v3.3.1 with Docker at https://conscio.dev", "external")
        cs.close()

        result = eng.enable_auto_index(run_kg_builder=True)
        assert result["auto_index"] is True
        assert result["initial_entities"] > 0 or result["initial_sources_scanned"] >= 1

        # Cleanup
        if eng._auto_indexer and eng._auto_indexer._installed:
            eng._auto_indexer.uninstall()
