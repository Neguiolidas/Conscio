"""TDD: Migration — export/import tar.gz round-trip."""
from conscio.content_store import ContentStore
from conscio.kg import KnowledgeGraph
from conscio.hallways import Hallways
from conscio.wings import WingManager
from conscio.migration import export_archive, import_archive


def test_export_creates_tarball(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    kg = KnowledgeGraph(db_path=tmp_path / "kg.db")
    wm.index(wing="projects", room="pentest", label="t1", content="vault scan", category="external")
    kg.add_entity("grolv", entity_type="domain")
    out = tmp_path / "backup.tar.gz"
    export_archive(out, content_store=cs, kg=kg, hallways=wm.hallways)
    assert out.exists()
    import tarfile
    assert tarfile.is_tarfile(out)


def test_export_metadata_json(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    wm.index(label="x", content="y", category="external", content_type="prose")
    out = tmp_path / "bk.tar.gz"
    export_archive(out, content_store=cs, hallways=wm.hallways)
    import tarfile, json
    with tarfile.open(out) as t:
        names = t.getnames()
        assert "metadata.json" in names
        meta_member = t.extractfile("metadata.json")
        meta = json.loads(meta_member.read().decode())
        assert "version" in meta
        assert "exported_at" in meta


def test_import_round_trip(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    cs = ContentStore(db_path=src / "cs.db")
    wm = WingManager(hallways_db=src / "hw.db", content_store=cs)
    kg = KnowledgeGraph(db_path=src / "kg.db")
    wm.index(wing="projects", room="pentest", label="t1", content="pentest vault grolv at firebase", category="external")
    kg.add_entity("grolv.com.br", entity_type="domain")
    out = tmp_path / "bk.tar.gz"
    export_archive(out, content_store=cs, kg=kg, hallways=wm.hallways)
    cs.close(); kg.close(); wm.hallways.close()
    dst = tmp_path / "dst"
    dst.mkdir()
    cs2, kg2, hw2 = import_archive(out, target_dir=dst)
    results = cs2.search("pentest vault", limit=5)
    assert len(results) >= 1
    ent = kg2.query_entity("grolv.com.br")
    assert ent is not None
    cs2.close(); kg2.close(); hw2.close()


def test_export_empty(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    hw = Hallways(db_path=tmp_path / "hw.db")
    out = tmp_path / "empty.tar.gz"
    export_archive(out, content_store=cs, hallways=hw)
    assert out.exists()


def test_import_idempotent_no_duplicate(tmp_path):
    """Importar arquivo 2x para mesmo target_dir não duplica: dedup via content_hash."""
    cs = ContentStore(db_path=tmp_path / "src.db")
    wm = WingManager(hallways_db=tmp_path / "src_hw.db", content_store=cs)
    wm.index(label="t1", content="unique content xyz", category="external")
    archive = tmp_path / "bk.tar.gz"
    export_archive(archive, content_store=cs, hallways=wm.hallways)
    cs.close(); wm.hallways.close()
    dst = tmp_path / "dst"
    dst.mkdir()
    cs2, _, _ = import_archive(archive, target_dir=dst)
    cs2.close()
    # Second import — new ContentStore opening same DB (already has the row)
    cs3, _, _ = import_archive(archive, target_dir=dst)
    results = cs3.search("unique content", limit=10)
    # content_hash dedup in ContentStore prevents duplicate rows
    assert len(results) == 1
    cs3.close()
