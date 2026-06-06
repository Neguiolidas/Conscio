# conscio/content_layer.py
"""
Content Layering — derives a ROUTINE/PROCESSING/INTUITION layer per content item
at query time (no schema change), used as a near-tie tiebreak in recall().

Origin: Noetic Helix layers (Noosphere-Manifold, CC BY-NC-SA 4.0). Operational
paraphrase: factual noise (ROUTINE, N-1), processed insight (PROCESSING, N),
unvalidated hypothesis (INTUITION, N+1).
"""
from __future__ import annotations

from enum import Enum


class ContentLayer(Enum):
    ROUTINE = "routine"        # N-1: factual/system noise
    PROCESSING = "processing"  # N:   insights, reflections, decisions
    INTUITION = "intuition"    # N+1: hypotheses, predictions, anomalies


# The CATEGORY carries the layer semantics (ContentStore content_type is only
# prose/code/metric/log — formatting, not layer). Every real VALID_CATEGORY is
# mapped here. content_type is a fallback for unrecognized categories only.
_LAYER_BY_CATEGORY = {
    "reflection": ContentLayer.PROCESSING,
    "consciousness": ContentLayer.PROCESSING,
    "error": ContentLayer.INTUITION,        # anomalies / surprises — unvalidated signal
    "system": ContentLayer.ROUTINE,
    "trading": ContentLayer.ROUTINE,
    "perception": ContentLayer.ROUTINE,
    "session": ContentLayer.ROUTINE,
    "external": ContentLayer.ROUTINE,
}
_ROUTINE_CONTENT_TYPES = {"metric", "log"}


def layer_of(category: str, content_type: str = "") -> ContentLayer:
    """
    Classify content into a layer. The CATEGORY is authoritative — it carries the
    layer semantics (reflections/consciousness → PROCESSING, errors/anomalies →
    INTUITION, system/trading/perception/session/external → ROUTINE). For an
    unrecognized category, a metric/log content_type still reads as ROUTINE;
    otherwise default PROCESSING.
    """
    if category in _LAYER_BY_CATEGORY:
        return _LAYER_BY_CATEGORY[category]
    if content_type in _ROUTINE_CONTENT_TYPES:
        return ContentLayer.ROUTINE
    return ContentLayer.PROCESSING


# Recall reorder constants. RRF rank is higher = better.
LAYER_EPSILON = 0.01    # rank-bucket width (~1% of RRF range); a one-line tunable
_LAYER_PRIORITY = {
    ContentLayer.PROCESSING: 2,
    ContentLayer.INTUITION: 1,
    ContentLayer.ROUTINE: 0,
}


def layer_sort_key(result):
    """
    Sort key for recall results: relevance first (bucketed by LAYER_EPSILON),
    then layer priority within a bucket, then exact rank. Non-destructive — layer
    only reorders near-ties; a high-rank ROUTINE result is never buried.

    `result` is any object exposing `.source_category`, `.content_type`, `.rank`.
    """
    layer = layer_of(result.source_category, result.content_type)
    bucket = int(result.rank / LAYER_EPSILON)
    return (-bucket, -_LAYER_PRIORITY[layer], -result.rank)
