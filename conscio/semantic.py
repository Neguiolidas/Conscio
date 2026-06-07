# conscio/semantic.py
"""
Semantic engine — embedding-projected antonym axes for contradiction by MEANING.

Embeddings give similarity, not polarity. Polarity comes from antonym axes:
an axis is a named pair of poles, each pole the centroid (mean) of its anchor
terms' embeddings. Two terms contradict on an axis iff they project onto
OPPOSITE poles, each with cosine >= AXIS_THRESHOLD and a >= AXIS_MARGIN lead
over the other pole (a near-equidistant term is neutral, not contradictory).
This generalizes: `crashed`, `unreachable` project to the negative availability
pole without being in any lexicon — similarity to a LABELED pole, used honestly.

Offline-degradable + dependency-free: cosine is pure Python (no numpy); the
default Ollama embedder is imported LAZILY (from .session_rag) only on first
use, so `import conscio.semantic` pulls in nothing heavy. available() probes
once and caches; everything degrades to lexical when False.

Theory: Claude_Sentience (Dave Shapiro) — ontological coherence.
"""
from __future__ import annotations

import math

from .axis_pack import load_axes

AXIS_THRESHOLD = 0.62        # projection onto a pole counts as "on that pole"
AXIS_MARGIN = 0.05           # a term must beat the OPPOSITE pole by this much


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity in [-1, 1]; 0.0 for degenerate inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticEngine:
    """Embedder wrapper + cosine + cached antonym-axis pole vectors.

    The Ollama embedder and any numpy use are avoided unless semantics actually
    fire; an explicitly injected embedder (e.g. a test stub) is trusted as-is.
    """

    def __init__(self, embedder=None, axes=None, axis_packs=None):
        self._embedder = embedder
        self._axes_arg = axes
        self._axis_packs = axis_packs
        self._available = None                       # cached probe result
        self._emb_cache: dict[str, list[float]] = {}
        self._pole_cache: dict | None = None         # axis -> {"pos": vec, "neg": vec}

    # --- embedder plumbing ---

    def _get_embedder(self):
        if self._embedder is None:
            try:
                from .session_rag import OllamaEmbedder
                self._embedder = OllamaEmbedder()
            except Exception:
                self._embedder = None
        return self._embedder

    def available(self) -> bool:
        """True iff the embedder returns a non-empty vector (probed once)."""
        if self._available is not None:
            return self._available
        emb = self._get_embedder()
        if emb is None:
            self._available = False
            return False
        try:
            self._available = bool(emb.embed("ping"))
        except Exception:
            self._available = False
        return self._available

    def embed(self, text: str) -> list[float]:
        """Embedding for `text`, cached per process; [] when unavailable."""
        key = (text or "").strip().lower()
        if key in self._emb_cache:
            return self._emb_cache[key]
        emb = self._get_embedder()
        if emb is None:
            return []
        try:
            vec = emb.embed(key)
        except Exception:
            vec = []
        # A failed embed caches [] deliberately: this engine is dream-scoped
        # (short-lived, off the hot path), so the empty result is sticky for the
        # process. _centroid/_pole_of treat [] as "skip", never a contradiction.
        self._emb_cache[key] = vec
        return vec

    def cosine(self, a: str, b: str) -> float:
        """Cosine similarity between two TEXTS (embeds both, cached)."""
        return _cosine(self.embed(a), self.embed(b))

    # --- axis poles ---

    def _axes(self) -> list[dict]:
        return self._axes_arg if self._axes_arg is not None else load_axes(self._axis_packs)

    def _centroid(self, terms: list[str]) -> list[float]:
        """Mean of the embeddings of `terms`; [] if none embed."""
        vecs = [v for v in (self.embed(t) for t in terms) if v]
        if not vecs:
            return []
        dim = len(vecs[0])
        acc = [0.0] * dim
        n = 0
        for v in vecs:
            if len(v) != dim:
                continue
            for i in range(dim):
                acc[i] += v[i]
            n += 1
        return [x / n for x in acc] if n else []

    def _poles(self) -> dict:
        """axis -> {"pos": centroid, "neg": centroid}. Cached. An axis whose
        poles cannot both be embedded is skipped."""
        if self._pole_cache is not None:
            return self._pole_cache
        poles: dict = {}
        for ax in self._axes():
            name = ax.get("axis", "")
            pos = self._centroid(ax.get("positive", []))
            neg = self._centroid(ax.get("negative", []))
            if name and pos and neg:
                poles[name] = {"pos": pos, "neg": neg}
        self._pole_cache = poles
        return poles

    def _pole_of(self, text: str):
        """Return (axis_name, 'pos'|'neg') the text projects onto with the
        required threshold + margin, else None (neutral). Best axis wins."""
        vec = self.embed(text)
        if not vec:
            return None
        best = None
        for name, pp in self._poles().items():
            cp = _cosine(vec, pp["pos"])
            cn = _cosine(vec, pp["neg"])
            if cp >= AXIS_THRESHOLD and (cp - cn) >= AXIS_MARGIN:
                cand = (name, "pos", cp)
            elif cn >= AXIS_THRESHOLD and (cn - cp) >= AXIS_MARGIN:
                cand = (name, "neg", cn)
            else:
                continue
            if best is None or cand[2] > best[2]:
                best = cand
        return (best[0], best[1]) if best else None

    def opposes(self, s1: str, s2: str) -> bool:
        """True iff s1/s2 project onto OPPOSITE poles of the SAME axis, each
        clearing AXIS_THRESHOLD with an AXIS_MARGIN lead. Offline → False."""
        if not self.available():
            return False
        p1 = self._pole_of(s1)
        p2 = self._pole_of(s2)
        if p1 is None or p2 is None:
            return False
        return p1[0] == p2[0] and p1[1] != p2[1]


class ContradictionDetector:
    """Lexical fast-path → semantic axis opposition. Offline → lexical only.

    The lexical layer REUSES coherence._relations_contradict (single source of
    truth for negation tokens — no _NEG_TOKENS duplication). relations and
    states share one contradiction rule: lexical negation OR axis opposition.
    """

    def __init__(self, semantic: "SemanticEngine | None" = None):
        self.semantic = semantic

    def _contradict(self, a: str, b: str) -> bool:
        from .coherence import _relations_contradict
        if _relations_contradict(a, b):          # offline-safe lexical negation
            return True
        if self.semantic is not None and self.semantic.available():
            return self.semantic.opposes(a, b)   # semantic axis opposition
        return False

    def relations_contradict(self, p1: str, p2: str) -> bool:
        return self._contradict(p1, p2)

    def states_contradict(self, s1: str, s2: str) -> bool:
        return self._contradict(s1, s2)
