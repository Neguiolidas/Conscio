"""generator_version field on StructuralSignal (v3.4 B3)."""
from conscio.structural import StructuralDistiller, StructuralSignal


def test_signal_has_generator_version_default():
    sig = StructuralSignal(
        source_path="/tmp", built_at_commit="abc",
        content_hash="h", node_count=1, link_count=0,
        hyperedges=(), communities=(),
    )
    assert hasattr(sig, "generator_version")
    assert sig.generator_version == "unknown"


def test_signal_generator_version_explicit():
    sig = StructuralSignal(
        source_path="/tmp", built_at_commit="abc",
        content_hash="h", node_count=1, link_count=0,
        hyperedges=(), communities=(),
        generator_version="graphify-1.2.0",
    )
    assert sig.generator_version == "graphify-1.2.0"


def test_distiller_reads_generator_version_from_graph():
    import json
    import os
    import tempfile
    graph = {
        "generator_version": "graphify-1.2.0",
        "nodes": [{"id": "n1", "label": "a.py"}],
        "hyperedges": [],
        "communities": [],
        "metadata": {"commit": "deadbeef"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(graph, f)
        f.flush()
        distiller = StructuralDistiller.from_path(f.name)
    sig = distiller.distill()
    assert sig.generator_version == "graphify-1.2.0"
    os.unlink(f.name)


def test_distiller_reads_generator_version_from_metadata():
    import json
    import os
    import tempfile
    graph = {
        "nodes": [{"id": "n1", "label": "a.py"}],
        "hyperedges": [],
        "communities": [],
        "metadata": {"commit": "deadbeef", "generator_version": "graphify-2.0"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(graph, f)
        f.flush()
        distiller = StructuralDistiller.from_path(f.name)
    sig = distiller.distill()
    assert sig.generator_version == "graphify-2.0"
    os.unlink(f.name)
