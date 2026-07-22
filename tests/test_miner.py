"""TDD: Miner — file + conversation ingestion."""
from conscio.miner import Miner
from conscio.wings import WingManager
from conscio.content_store import ContentStore


def test_miner_file(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    f = tmp_path / "doc.md"
    f.write_text("# Pentest Vault\n\nFound bug in Firebase auth at vault.grolv.com.br.")
    count = m.ingest_file(f, wing="projects", room="pentest")
    assert count >= 1
    results = wm.search("pentest vault", wing="projects", limit=5)
    assert len(results) >= 1


def test_miner_jsonl_convo(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    f = tmp_path / "convo.jsonl"
    f.write_text('{"role":"user","content":"What is Conscio?"}\n{"role":"assistant","content":"Conscio is a consciousness framework for agents."}\n')
    count = m.ingest_conversation(f, wing="conscio", room="general")
    assert count >= 2


def test_miner_skips_binary(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00\x01\x02\xff\xfe\x00")
    count = m.ingest_file(f, wing="projects", room="misc")
    assert count == 0


def test_miner_dedup(tmp_path):
    """Second ingest of same file skips."""
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    f = tmp_path / "doc.md"
    f.write_text("unique content xyz")
    c1 = m.ingest_file(f, wing="a", room="b")
    assert c1 == 1
    c2 = m.ingest_file(f, wing="a", room="b")
    assert c2 == 0


def test_miner_directory(tmp_path):
    """Ingest all .md from directory."""
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("file A about pentest")
    (d / "b.md").write_text("file B about firebase")
    count = m.ingest_directory(d, wing="docs", room="general")
    assert count == 2


def test_miner_directory_skip_dirs(tmp_path):
    """Skip .git, __pycache__, etc."""
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    d = tmp_path / "proj"
    d.mkdir()
    (d / "real.md").write_text("real content")
    sub = d / ".git"
    sub.mkdir()
    (sub / "config.md").write_text("git internal")  # should be skipped
    pycache = d / "__pycache__"
    pycache.mkdir()
    (pycache / "cache.md").write_text("cache")  # should be skipped
    count = m.ingest_directory(d, wing="p", room="r")
    assert count == 1


def test_miner_txt_file(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    f = tmp_path / "doc.txt"
    f.write_text("plain text file content")
    count = m.ingest_file(f, wing="docs", room="notes")
    assert count == 1


def test_miner_unsupported_ext(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    m = Miner(wing_manager=wm)
    f = tmp_path / "doc.rtf"
    f.write_text("rtf content")
    count = m.ingest_file(f, wing="x", room="y")
    assert count == 0
