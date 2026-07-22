"""TDD: Import MemPalace format."""
from conscio.content_store import ContentStore
from conscio.wings import WingManager
from conscio.migration import import_format_mempalace


def test_import_mempalace_missing_dir(tmp_path):
    """Nonexistent dir returns 0."""
    count = import_format_mempalace(tmp_path / "nope", wing_manager=None)
    assert count == 0


def test_import_mempalace_no_chroma(tmp_path):
    """Dir exists but no chroma.sqlite3 returns 0."""
    d = tmp_path / "mp"
    d.mkdir()
    count = import_format_mempalace(d, wing_manager=None)
    assert count == 0


def test_import_mempalace_with_data(tmp_path):
    """Create a minimal ChromaDB-like SQLite and import."""
    import sqlite3
    d = tmp_path / "mp"
    d.mkdir()
    # Create a minimal chroma.sqlite3 mimicking MemPalace structure
    # tables: embedding_metadata (id FK->embeddings.id, key, value)
    #         embeddings (id, embedding_id, ...)
    db = sqlite3.connect(str(d / "chroma.sqlite3"))
    db.executescript("""
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY,
            embedding_id TEXT,
            embedding BLOB
        );
        CREATE TABLE embedding_metadata (
            id INTEGER,
            key TEXT,
            value TEXT,
            FOREIGN KEY (id) REFERENCES embeddings(id)
        );
    """)
    # Insert 2 embeddings + metadata with chroma:document, wing, room
    db.execute("INSERT INTO embeddings (id, embedding_id) VALUES (1, 'vec1')")
    db.execute("INSERT INTO embeddings (id, embedding_id) VALUES (2, 'vec2')")
    # Drawer 1: wing=projects, room=pentest
    db.execute("INSERT INTO embedding_metadata (id, key, value) VALUES (1, 'chroma:document', 'Pentest of vault.grolv.com.br')")
    db.execute("INSERT INTO embedding_metadata (id, key, value) VALUES (1, 'wing', 'projects')")
    db.execute("INSERT INTO embedding_metadata (id, key, value) VALUES (1, 'room', 'pentest')")
    # Drawer 2: wing=conscio, room=general
    db.execute("INSERT INTO embedding_metadata (id, key, value) VALUES (2, 'chroma:document', 'Conscio engine reflect scene')")
    db.execute("INSERT INTO embedding_metadata (id, key, value) VALUES (2, 'wing', 'conscio')")
    db.execute("INSERT INTO embedding_metadata (id, key, value) VALUES (2, 'room', 'general')")
    db.commit()
    db.close()

    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    count = import_format_mempalace(d, wing_manager=wm)
    assert count == 2
    # Verify search
    r1 = wm.search("pentest vault", wing="projects", limit=5)
    assert len(r1) >= 1
    r2 = wm.search("engine reflect", wing="conscio", limit=5)
    assert len(r2) >= 1
    wm.close()
