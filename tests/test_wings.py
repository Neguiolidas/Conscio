"""TDD: Wings — integrate Hallways + ContentStore."""
from conscio.content_store import ContentStore
from conscio.wings import WingManager


def test_wings_index_with_wing(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    drawer_id = wm.index(
        wing="projects", room="pentest",
        label="vault_scan", content="Pentest do vault.grolv.com.br",
        category="external", content_type="prose"
    )
    assert drawer_id > 0
    drawers = wm.list_drawers("projects", "pentest")
    assert drawer_id in drawers
    wm.close()


def test_wings_search_by_wing(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    wm.index(wing="projects", room="pentest", label="t1", content="firebase auth signup", category="external")
    wm.index(wing="conscio", room="general", label="t2", content="engine reflect scene", category="external")
    results = wm.search("firebase", wing="projects", limit=5)
    assert len(results) >= 1
    assert "firebase" in results[0].content.lower()
    wm.close()


def test_wings_default(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    drawer_id = wm.index(label="x", content="y", category="external", content_type="prose")
    drawers = wm.list_drawers("default", "default")
    assert drawer_id in drawers
    wm.close()


def test_wings_list_all(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    wm.index(wing="a", room="x", label="1", content="c1", category="external")
    wm.index(wing="b", room="y", label="2", content="c2", category="external")
    wings = wm.list_wings()
    assert "a" in wings and "b" in wings
    wm.close()


def test_wings_search_no_wing(tmp_path):
    """Search sem wing — busca em todos drawers."""
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    wm.index(wing="a", room="x", label="1", content="keyword unicorn", category="external")
    wm.index(wing="b", room="y", label="2", content="other text", category="external")
    results = wm.search("unicorn", wing=None, limit=5)
    assert len(results) == 1
    assert "unicorn" in results[0].content.lower()
    wm.close()


def test_wings_rooms(tmp_path):
    cs = ContentStore(db_path=tmp_path / "cs.db")
    wm = WingManager(hallways_db=tmp_path / "hw.db", content_store=cs)
    wm.index(wing="projects", room="pentest", label="1", content="content", category="external")
    wm.index(wing="projects", room="security", label="2", content="content", category="external")
    rooms = wm.list_rooms("projects")
    assert "pentest" in rooms and "security" in rooms
    wm.close()
