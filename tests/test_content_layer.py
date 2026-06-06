# tests/test_content_layer.py
from types import SimpleNamespace
from conscio.content_layer import ContentLayer, layer_of, layer_sort_key, LAYER_EPSILON


def _result(category, content_type, rank):
    return SimpleNamespace(source_category=category, content_type=content_type, rank=rank)


def test_layer_of_routine():
    assert layer_of("system", "prose") is ContentLayer.ROUTINE
    assert layer_of("trading", "prose") is ContentLayer.ROUTINE


def test_layer_of_processing():
    assert layer_of("consciousness", "reflection") is ContentLayer.PROCESSING
    assert layer_of("external", "insight") is ContentLayer.PROCESSING


def test_layer_of_intuition():
    assert layer_of("consciousness", "prediction") is ContentLayer.INTUITION
    assert layer_of("system", "anomaly") is ContentLayer.INTUITION


def test_layer_of_content_type_wins_over_category():
    # A prediction in the 'system' category is INTUITION, not ROUTINE.
    assert layer_of("system", "prediction") is ContentLayer.INTUITION


def test_layer_of_unknown_defaults_processing():
    assert layer_of("external", "prose") is ContentLayer.PROCESSING


def test_sort_prefers_higher_layer_within_epsilon_bucket():
    routine = _result("system", "prose", 0.030)                  # ROUTINE
    processing = _result("consciousness", "reflection", 0.029)   # PROCESSING, same bucket
    ordered = sorted([routine, processing], key=layer_sort_key)
    assert ordered[0] is processing                              # layer wins the near-tie


def test_sort_relevance_wins_across_buckets():
    routine = _result("system", "prose", 0.030)                  # ROUTINE, high rank
    processing = _result("consciousness", "reflection", 0.010)   # PROCESSING, lower bucket
    ordered = sorted([routine, processing], key=layer_sort_key)
    assert ordered[0] is routine                                 # relevance wins; not buried


def test_sort_rank_zero_boundary_layer_decides():
    routine = _result("system", "prose", 0.0)
    processing = _result("consciousness", "reflection", 0.0)
    ordered = sorted([routine, processing], key=layer_sort_key)
    assert ordered[0] is processing                              # bucket-0 collapse -> layer decides


def test_epsilon_is_tunable_constant():
    assert LAYER_EPSILON == 0.01
