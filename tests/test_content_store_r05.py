# tests/test_content_store_r05.py
"""R-05: ContentStore.index() chunk-level metadata-aware dedup. Re-indexing the
same text under a new category makes it findable there; same category is a
no-op; label stays first-seen provenance."""
from conscio.content_store import ContentStore


def _cs(tmp_path):
    return ContentStore(db_path=tmp_path / "conscio.db")


def test_reindex_under_new_category_is_searchable(tmp_path):
    cs = _cs(tmp_path)
    text = "the breaker tripped on a deploy"
    sid1 = cs.index(label="a", content=text, category="system")
    sid2 = cs.index(label="b", content=text, category="external")
    assert sid1 == sid2                                  # source still deduped
    assert cs.search("breaker", category="external")     # now findable
    assert cs.search("breaker", category="system")       # still findable
    cs.close()


def test_same_category_is_noop(tmp_path):
    cs = _cs(tmp_path)
    text = "hello world"
    cs.index(label="a", content=text, category="system", session_id="s1")
    before = len(cs.search("hello", category="system"))
    cs.index(label="z", content=text, category="system", session_id="s2")
    after = len(cs.search("hello", category="system"))
    assert before == after                               # no duplicate chunks
    cs.close()
