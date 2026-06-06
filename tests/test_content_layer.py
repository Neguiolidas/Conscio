# tests/test_content_layer.py
from types import SimpleNamespace
from conscio.content_layer import ContentLayer, layer_of, layer_sort_key, LAYER_EPSILON


def _result(category, content_type, rank):
    return SimpleNamespace(source_category=category, content_type=content_type, rank=rank)


def test_layer_of_routine():
    assert layer_of("system", "prose") is ContentLayer.ROUTINE
    assert layer_of("trading", "metric") is ContentLayer.ROUTINE
    assert layer_of("perception", "prose") is ContentLayer.ROUTINE
    assert layer_of("session", "log") is ContentLayer.ROUTINE
    assert layer_of("external", "prose") is ContentLayer.ROUTINE


def test_layer_of_processing():
    assert layer_of("reflection", "prose") is ContentLayer.PROCESSING
    assert layer_of("consciousness", "prose") is ContentLayer.PROCESSING


def test_layer_of_intuition():
    # 'error' is the only unvalidated-signal category (anomalies/surprises).
    assert layer_of("error", "prose") is ContentLayer.INTUITION


def test_layer_of_category_is_authoritative():
    # category decides even when content_type would otherwise read as routine.
    assert layer_of("reflection", "log") is ContentLayer.PROCESSING


def test_layer_of_unknown_category_metric_log_routine():
    assert layer_of("mystery", "metric") is ContentLayer.ROUTINE
    assert layer_of("mystery", "log") is ContentLayer.ROUTINE


def test_layer_of_unknown_defaults_processing():
    assert layer_of("mystery", "prose") is ContentLayer.PROCESSING


def test_sort_prefers_higher_layer_within_epsilon_bucket():
    # Both in floor-bucket 3 (int(0.039/0.01)==int(0.031/0.01)==3) — mid-bucket, no
    # boundary straddle. ROUTINE has the higher raw rank, yet PROCESSING wins the band.
    routine = _result("system", "prose", 0.039)                  # ROUTINE, higher rank
    processing = _result("consciousness", "prose", 0.031)   # PROCESSING, same bucket
    ordered = sorted([routine, processing], key=layer_sort_key)
    assert ordered[0] is processing                              # layer wins the near-tie


def test_sort_relevance_wins_across_buckets():
    routine = _result("system", "prose", 0.030)                  # ROUTINE, high rank
    processing = _result("consciousness", "prose", 0.010)   # PROCESSING, lower bucket
    ordered = sorted([routine, processing], key=layer_sort_key)
    assert ordered[0] is routine                                 # relevance wins; not buried


def test_sort_rank_zero_boundary_layer_decides():
    routine = _result("system", "prose", 0.0)
    processing = _result("consciousness", "prose", 0.0)
    ordered = sorted([routine, processing], key=layer_sort_key)
    assert ordered[0] is processing                              # bucket-0 collapse -> layer decides


def test_epsilon_is_tunable_constant():
    assert LAYER_EPSILON == 0.01
