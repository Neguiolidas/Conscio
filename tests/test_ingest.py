"""Tests for engine.ingest_directory() and the `conscio ingest` CLI subcommand."""
from __future__ import annotations

import pytest

from conscio.cli import main
from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine


@pytest.fixture
def engine(tmp_path):
    storage = tmp_path / "storage"
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
    e.content_layer._session_rag = _RAG_DISABLED  # hermetic: no Ollama probe
    yield e
    e.close()


def _make_corpus(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.md").write_text(
        "# Heading One\n\nSome markdown body text.\n\n## Heading Two\n\nMore body text.\n",
        encoding="utf-8",
    )
    (root / "config.yaml").write_text(
        "key: value\nother: 1\n---\nsecond: doc\n",
        encoding="utf-8",
    )
    (root / "plain.txt").write_text(
        "Just a plain prose paragraph about something interesting.",
        encoding="utf-8",
    )
    return root


# ─── engine.ingest_directory() ──────────────────────────────────────────

class TestIngestDirectory:
    def test_ingests_all_files_in_directory(self, engine, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")
        result = engine.ingest_directory(corpus)

        assert result["total"] == 3
        assert result["ingested"] == 3
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["duration_s"] >= 0.0
        assert engine.content_store.stats()["source_count"] == 3

    def test_content_type_detected_by_extension(self, engine, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")
        engine.ingest_directory(corpus, category="reference")

        rows = engine.content_store.db.execute(
            "SELECT title, content_type FROM chunks"
        ).fetchall()
        types_by_label_prefix = {}
        for r in rows:
            title = r["title"]
            if "notes.md" in title:
                types_by_label_prefix["md"] = r["content_type"]
            elif "config.yaml" in title:
                types_by_label_prefix["yaml"] = r["content_type"]
            elif "plain.txt" in title:
                types_by_label_prefix["txt"] = r["content_type"]

        assert types_by_label_prefix["md"] == "markdown"
        assert types_by_label_prefix["yaml"] == "yaml"
        assert types_by_label_prefix["txt"] == "prose"

    def test_category_is_applied(self, engine, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")
        engine.ingest_directory(corpus, category="pentest")

        cats = {
            r["source_category"]
            for r in engine.content_store.db.execute(
                "SELECT DISTINCT source_category FROM chunks"
            ).fetchall()
        }
        assert cats == {"pentest"}

    def test_single_file_ingestion(self, engine, tmp_path):
        f = tmp_path / "solo.md"
        f.write_text("# Solo\n\nJust one file.\n", encoding="utf-8")
        result = engine.ingest_directory(f)

        assert result["total"] == 1
        assert result["ingested"] == 1

    def test_nonexistent_path_returns_zeroed_result(self, engine, tmp_path):
        result = engine.ingest_directory(tmp_path / "does-not-exist")
        assert result == {"total": 0, "ingested": 0, "duplicate": 0,
                          "skipped": 0, "failed": 0,
                          "duration_s": result["duration_s"]}
        assert result["total"] == 0

    def test_reingest_of_unchanged_corpus_counts_duplicates_not_ingested(
        self, engine, tmp_path
    ):
        """A no-op re-run must be distinguishable from a real ingest.

        Regression guard for the review finding: index() used to return only a
        source_id, so ingest_directory counted every already-indexed file as
        "ingested" — a second run over an unchanged corpus reported the same
        headline number as the first, making that number useless as evidence.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        for i in range(3):
            (corpus / f"doc{i}.md").write_text(f"# Doc {i}\n\nbody {i}\n", encoding="utf-8")

        first = engine.ingest_directory(corpus)
        assert first["ingested"] == 3
        assert first["duplicate"] == 0

        second = engine.ingest_directory(corpus)
        assert second["total"] == 3
        assert second["ingested"] == 0
        assert second["duplicate"] == 3
        assert second["failed"] == 0

    def test_empty_file_is_skipped(self, engine, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "empty.txt").write_text("", encoding="utf-8")
        (corpus / "whitespace.txt").write_text("   \n\n  ", encoding="utf-8")
        (corpus / "real.txt").write_text("actual content here", encoding="utf-8")

        result = engine.ingest_directory(corpus)

        assert result["total"] == 3
        assert result["skipped"] == 2
        assert result["ingested"] == 1

    def test_reingest_same_corpus_does_not_duplicate_chunks(self, engine, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")
        engine.ingest_directory(corpus)
        chunk_count_first = engine.content_store.stats()["chunk_count"]

        engine.ingest_directory(corpus)  # re-run, unchanged content
        chunk_count_second = engine.content_store.stats()["chunk_count"]

        assert chunk_count_second == chunk_count_first
        assert engine.content_store.stats()["source_count"] == 3

    def test_progress_callback_invoked_per_file(self, engine, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")
        calls = []

        engine.ingest_directory(
            corpus, progress_callback=lambda processed, total: calls.append((processed, total))
        )

        assert len(calls) == 3
        assert calls[-1] == (3, 3)

    def test_progress_callback_exception_does_not_break_ingest(self, engine, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")

        def bad_callback(processed, total):
            raise RuntimeError("boom")

        result = engine.ingest_directory(corpus, progress_callback=bad_callback)
        assert result["ingested"] == 3

    def test_chunk_size_and_overlap_passed_through(self, engine, tmp_path):
        big = tmp_path / "big.md"
        big.write_text(
            "\n\n".join(f"## Section {i}\n\nBody text {i} " + ("x" * 100) for i in range(20)),
            encoding="utf-8",
        )
        result = engine.ingest_directory(big, chunk_size=200, overlap=0.1)
        assert result["ingested"] == 1
        chunk_count = engine.content_store.db.execute(
            "SELECT COUNT(*) as c FROM chunks"
        ).fetchone()["c"]
        assert chunk_count > 1  # confirms chunk_size actually took effect

    def test_default_category_is_reference(self, engine, tmp_path):
        f = tmp_path / "solo.txt"
        f.write_text("content", encoding="utf-8")
        engine.ingest_directory(f)
        row = engine.content_store.db.execute(
            "SELECT source_category FROM chunks LIMIT 1"
        ).fetchone()
        assert row["source_category"] == "reference"


# ─── vector side-effect (best-effort, degrades gracefully offline) ──────

class TestIngestVectorSideEffect:
    def test_ingest_does_not_crash_without_embedder(self, engine, tmp_path):
        """This environment has no sentence-transformers/Ollama — ingest must
        still fully succeed via the FTS5 path (vector embedding is best-effort)."""
        corpus = _make_corpus(tmp_path / "corpus")
        result = engine.ingest_directory(corpus)
        assert result["ingested"] == 3
        assert result["failed"] == 0


# ─── CLI `conscio ingest` ────────────────────────────────────────────────

class TestIngestCLI:
    def test_cli_ingest_directory(self, tmp_path, capsys):
        corpus = _make_corpus(tmp_path / "corpus")
        storage = tmp_path / "storage"

        rc = main(["ingest", str(corpus), "--storage", str(storage)])

        assert rc == 0
        out = capsys.readouterr().out
        assert "ingest complete" in out
        assert "3 ingested" in out

    def test_cli_ingest_custom_category(self, tmp_path, capsys):
        corpus = _make_corpus(tmp_path / "corpus")
        storage = tmp_path / "storage"

        rc = main(["ingest", str(corpus), "--category", "pentest",
                  "--storage", str(storage)])
        assert rc == 0

        eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
        try:
            cats = {
                r["source_category"]
                for r in eng.content_store.db.execute(
                    "SELECT DISTINCT source_category FROM chunks"
                ).fetchall()
            }
            assert cats == {"pentest"}
        finally:
            eng.close()

    def test_cli_ingest_emits_host_event(self, tmp_path):
        corpus = _make_corpus(tmp_path / "corpus")
        storage = tmp_path / "storage"

        rc = main(["ingest", str(corpus), "--storage", str(storage)])
        assert rc == 0

        eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
        try:
            events = eng.event_bus.query(type="host:event", limit=10)
            assert any(
                isinstance(e.data, dict) and e.data.get("event") == "ingest_directory"
                for e in events
            )
        finally:
            eng.close()

    def test_cli_ingest_chunk_size_and_overlap_flags(self, tmp_path, capsys):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.txt").write_text("hello world content", encoding="utf-8")
        storage = tmp_path / "storage"

        rc = main(["ingest", str(corpus), "--chunk-size", "500", "--overlap", "0.3",
                  "--storage", str(storage)])
        assert rc == 0
        assert "1 ingested" in capsys.readouterr().out
