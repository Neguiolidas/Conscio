"""
session_lifecycle.py — Semantic compression bridge between Conscio and agent frameworks.

Philosophy: handoff = semantic map, NOT transcript.
Chunks are dense, tag-rich, short — like embedding metadata.
Goal: maximum information density with minimum context weight.

Called by hook handlers on session:end / session:reset.
Supports configurable paths via `handoff_dir` and `session_db` parameters.
"""

from __future__ import annotations

import sqlite3
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
SESSION_DB = HERMES_HOME / "state.db"
HANDOFF_DIR = Path.home() / ".conscio" / "handoff"
HANDOFF_PATH = HANDOFF_DIR / "_session_handoff.md"
HEARTBEAT_PATH = HANDOFF_DIR / "_latest_heartbeat.md"

_ENV_SESSION_DB = os.environ.get("CONSCIO_SESSION_DB")
if _ENV_SESSION_DB:
    SESSION_DB = Path(_ENV_SESSION_DB)

_CONSCIO_HANDOFF_DIR = os.environ.get("CONSCIO_HANDOFF_DIR")
if _CONSCIO_HANDOFF_DIR:
    if _CONSCIO_HANDOFF_DIR.lower() in ("none", "off", "false", "0"):
        HANDOFF_DIR = None  # type: ignore[assignment]
        HANDOFF_PATH = None  # type: ignore[assignment]
        HEARTBEAT_PATH = None  # type: ignore[assignment]
    else:
        HANDOFF_DIR = Path(_CONSCIO_HANDOFF_DIR)
        HANDOFF_PATH = HANDOFF_DIR / "_session_handoff.md"
        HEARTBEAT_PATH = HANDOFF_DIR / "_latest_heartbeat.md"

# Noise patterns — system-injected messages, never real user input
SKIP_PREFIXES = [
    "[CONTEXT COMPACTION",
    "[Your active task",
    "[IMPORTANT: Background",
    "[System note:",
    "[IMPORTANT: You are running as a scheduled cron",
    "[HEARTBEAT — Contexto da sessão anterior",
    "[FIM DO HEARTBEAT",
]

NOISE_PATTERNS = [
    r"\[CONTEXT COMPACTION[^\]]*\]",
    r"\[Your active task list was preserved[^\]]*\]",
    r"\[IMPORTANT: Background[^\]]*\]",
    r"\[System note:[^\]]*\]",
    r"\[HEARTBEAT[^\]]*\]",
    r"\[FIM DO HEARTBEAT[^\]]*\]",
]

# Hard limits for semantic chunks
HB_MAX_CHARS = 1200  # heartbeat — must stay lean
HO_MAX_CHARS = 3000  # handoff — richer but still bounded
MAX_CHUNKS = 8       # max semantic chunks per output

# Legacy compat constants (used by tests)
MAX_USER_INTENTS = MAX_CHUNKS
MAX_ASSISTANT_ACTIONS = MAX_CHUNKS
MAX_REASONING = 4
HB_MAX_INTENTS = 4
HB_MAX_ACTIONS = 3
HB_MAX_TOPICS = 5

_UNSET: Any = object()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SemanticChunk:
    """A single dense information unit — like a ChromaDB document."""
    tag: str           # e.g. "fix", "decision", "task", "state"
    domain: str  # e.g. "trading", "conscio", "infra", "general"
    payload: str       # ultra-short: "warmup_mode_added|3ticks_hold"
    source_role: str   # "user" or "assistant"


@dataclass
class SessionSummary:
    """Extracted semantic map from a session."""
    session_id: str = ""
    model: str = ""
    started_at: str = ""
    message_count: int = 0
    title: str = ""
    chunks: list[SemanticChunk] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    # Conscio enrichment
    world_model_entities: list[str] = field(default_factory=list)
    active_goals: list[str] = field(default_factory=list)
    meta_confidence: float = 0.0
    stale_entities: list[str] = field(default_factory=list)
    trajectory: str = ""
    vibes: str = ""
    identity_anchor: str = ""
    coherence: Optional[float] = None
    coherence_note: str = ""
    voice: str = ""
    self_prompt: str = ""
    dream_recommended: str = ""

    # Legacy compat fields (accepted in __init__ for test compat, derived from chunks when not set)
    _intents: list[str] = field(default_factory=list)
    _actions: list[str] = field(default_factory=list)
    _reasoning: list[str] = field(default_factory=list)

    # Allow creating with intents= kwarg (test compat)
    def __init__(self, **kwargs):
        # Extract legacy fields before dataclass init
        legacy_intents = kwargs.pop("intents", None)
        legacy_actions = kwargs.pop("actions", None)
        legacy_reasoning = kwargs.pop("reasoning", None)

        # Set defaults for all dataclass fields
        all_fields = {
            "session_id": "", "model": "", "started_at": "", "message_count": 0,
            "title": "", "chunks": [], "topics": [],
            "world_model_entities": [], "active_goals": [], "meta_confidence": 0.0,
            "stale_entities": [], "trajectory": "", "vibes": "", "identity_anchor": "",
            "coherence": None, "coherence_note": "", "voice": "",
            "self_prompt": "", "dream_recommended": "",
            "_intents": [], "_actions": [], "_reasoning": [],
        }
        all_fields.update(kwargs)
        for k, v in all_fields.items():
            setattr(self, k, v)

        # Store legacy overrides
        if legacy_intents is not None:
            self._intents = legacy_intents
        if legacy_actions is not None:
            self._actions = legacy_actions
        if legacy_reasoning is not None:
            self._reasoning = legacy_reasoning

    # Legacy compat properties
    @property
    def intents(self) -> list[str]:
        if self._intents:
            return self._intents
        return [c.payload for c in self.chunks if c.source_role == "user"]

    @intents.setter
    def intents(self, val):
        self._intents = val

    @property
    def actions(self) -> list[str]:
        if self._actions:
            return self._actions
        return [c.payload for c in self.chunks if c.source_role == "assistant"]

    @actions.setter
    def actions(self, val):
        self._actions = val

    @property
    def reasoning(self) -> list[str]:
        if self._reasoning:
            return self._reasoning
        return [f"[{c.tag}] {c.payload}" for c in self.chunks
                if c.tag in ("decision", "bug", "fix")]

    @reasoning.setter
    def reasoning(self, val):
        self._reasoning = val


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

def strip_noise(text: str) -> str:
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.DOTALL)
    return text.strip()


def is_noise(content: str) -> bool:
    return any(content.startswith(prefix) for prefix in SKIP_PREFIXES)


# ---------------------------------------------------------------------------
# Semantic extraction — chunk-based, NOT transcript
# ---------------------------------------------------------------------------

# Tag patterns: regex → (tag, domain)
TAG_PATTERNS = [
    # Actions / fixes
    (r"(?i)(corrigi|fixei|resolvi|consertei|patch|consertar|fix|fixed|resolved|reparado)", "fix"),
    (r"(?i)(adicion|implement|criei|create|added|new|escrevi|build|built)", "create"),
    (r"(?i)(refator|refactor|mudei|mov|renome|delete|remov|clean|limp)", "change"),
    (r"(?i)(test|verifiquei|confirmei|valid|check|passou|failed|falhou)", "verify"),
    # Decisions
    (r"(?i)(decid|escolh|optei|porque|razão|motivo|trade.?off|compromisso)", "decision"),
    (r"(?i)(não fazer|skip|ignorar|evitar|never|don't|NÃO)", "decision"),
    # Problems
    (r"(?i)(bug|erro|error|fail|traceback|exception|crash|timeout|broken)", "bug"),
    (r"(?i)(problema|issue|não funciona|não tá|offline|down|stuck)", "bug"),
    # State / config
    (r"(?i)(saldo|balance|bankroll|p&l|pnl|winrate|posição|position|usdt)", "state"),
    (r"(?i)(config|setup|instal|deploy|cron|hook|skill|provider|modelo)", "config"),
    (r"(?i)(warmup|tick|engine|bot|gateway|server|daemon)", "state"),
    # Requests (user intent)
    (r"(?i)(quero|preciso|faça|faz|pode|create|build|reset|atualiz|mudar)", "request"),
    (r"(?i)(queria|gostaria|seria|como|explique|me fala|investiga)", "request"),
]

DOMAIN_KEYWORDS = {
    "trading": ["trading", "okx", "swap", "pnl", "position", "bankroll", "confluence", "usdt", "btc", "eth", "short", "long", "winrate"],
    "agent": ["agent", "skill", "hook", "cron", "gateway", "provider", "model", "session", "handoff", "heartbeat"],
    "conscio": ["conscio", "consciousness", "world model", "goals", "reflect", "dream", "coherence", "trajectory"],
    "infra": ["server", "deploy", "docker", "nginx", "systemd", "ssh", "vm", "oracle", "cloud", "load", "cpu"],
    "debug": ["bug", "error", "fix", "traceback", "fail", "broken", "debug"],
    "code": ["refactor", "code", "test", "pipeline", "function", "class", "module", "api"],
}

def _extract_keywords(text: str, max_kw: int = 5) -> list[str]:
    """Extract meaningful keywords from text — stopwords stripped."""
    STOP = {
        # PT articles/prepositions/conjunctions
        "o","a","os","as","um","uma","uns","umas","de","do","da","dos","das",
        "em","no","na","nos","nas","por","pra","pro","para","com","sem",
        "que","se","e","é","ou","mas","não","nem","como","quando","onde",
        "ao","à","às","pelos","pelas","pelo","pela","seu","sua","seus","suas",
        "meu","minha","meus","minhas","teu","tua","teus","tuas","nosso",
        "isso","isto","aquilo","ele","ela","eles","elas","você","vocês",
        "entre","sobre","após","antes","já","ainda","também","só","mesmo",
        "tudo","nada","algo","cada","todo","toda","todos","todas","mais","muito",
        "muita","pouco","pouca","outro","outra","outros","outras","qual","quais",
        "quem","cujo","cuja","cujos","cujas","tal","tais","quanto","quantos",
        "tantos","tanta","tantas","vez","vezes","bem","mal","melhor","pior",
        "ser","estar","ter","haver","ir","vir","fazer","dizer","ver","dar",
        "preciso","precisa","quero","quer","foi","foram","tem","tinha","ter",
        "devemos","deve","devia","podemos","pode","podia","vamos","vai","vão",
        # EN stopwords
        "the","a","an","is","are","was","were","be","been","to","of","and",
        "in","that","it","for","on","with","as","by","at","from","or","this",
        "but","not","have","has","had","do","does","did","will","would","can",
        "could","should","may","might","shall","if","then","than","so","no",
        "up","out","just","also","more","some","any","all","each","every",
        "very","too","also","only","about","into","over","after","before",
        # Greetings/fillers (high-frequency, zero info)
        "bom","boa","dia","tarde","noite","olá","hello","hi","hey",
        "obrigado","obrigada","valeu","thanks","thank","please",
        "entendi","entende","ok","tá","ta","sim","claro","certo",
        "senhor","senhora","hermet","hermes",
        "vou","vamos","deixe","deixar","aqui","agora","hoje",
        "verificar","ver","verificar","funcionou","feito",
        "panorama","diagnóstico","pronto","resumo","relatou",
    }
    # Strip markdown, code, punctuation, URLs
    text = re.sub(r'[*#`_\[\]():{},.!?;]', ' ', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    words = re.findall(r'[a-zA-ZÀ-ÿ_][-a-zA-ZÀ-ÿ_0-9]*', text)
    keywords = []
    seen = set()
    for w in words:
        lw = w.lower()
        if lw in STOP or len(lw) < 3:
            continue
        if lw not in seen:
            seen.add(lw)
            keywords.append(w)  # preserve original case
        if len(keywords) >= max_kw:
            break
    return keywords


def compress_message(content: str, role: str) -> SemanticChunk | None:
    """Compress a single message into a semantic chunk — concept-level, not phrase-level.
    Payload format: keyword1+keyword2|optional_context_hint (max 60 chars)"""
    content = strip_noise(content)
    if not content or len(content) < 8:
        return None

    # Determine tag
    tag = "info"
    for pattern, t in TAG_PATTERNS:
        if re.search(pattern, content):
            tag = t
            break

    # Determine domain
    lower = content.lower()
    domain = "general"
    best_overlap = 0
    for d, keywords in DOMAIN_KEYWORDS.items():
        overlap = sum(1 for kw in keywords if kw in lower)
        if overlap > best_overlap:
            best_overlap = overlap
            domain = d

    # Extract conceptual keywords — this is the key innovation
    kws = _extract_keywords(content, max_kw=5)
    if not kws:
        return None

    # Build dense payload: kw1·kw2·kw3 (dot-separated, like ChromaDB tags)
    payload = '·'.join(kws[:4])
    # Add action hint if space allows
    if tag in ("fix", "bug", "create", "decision") and len(payload) < 45:
        payload += f'|{tag}'

    # Hard cap 60 chars
    payload = payload[:60]

    return SemanticChunk(tag=tag, domain=domain, payload=payload, source_role=role)


def extract_chunks(messages: list[dict]) -> list[SemanticChunk]:
    """Extract semantic chunks from messages — dense, tag-rich, short."""
    chunks: list[SemanticChunk] = []
    seen_signatures: set[str] = set()

    for msg in messages:
        content = msg.get("content_preview", "") or ""
        if is_noise(content):
            continue
        role = msg.get("role", "?")
        chunk = compress_message(content, role)
        if chunk is None:
            continue
        # Deduplicate by signature (tag+domain+payload), but allow
        # different payloads even with same keywords (e.g. "request 1" vs "request 5")
        sig = f"{chunk.tag}:{chunk.domain}:{chunk.payload}"
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)
        chunks.append(chunk)
        if len(chunks) >= MAX_CHUNKS:
            break

    return chunks


def infer_topics(intents_or_messages, actions=None) -> list[str]:
    """Infer conversation topics.
    - Legacy: infer_topics(intents, actions) — two string lists
    - New: infer_topics(chunks) — SemanticChunk list
    - Fallback: infer_topics(messages) — dict list
    """
    # Legacy two-arg form: infer_topics(intents_list, actions_list)
    if actions is not None:
        all_text = " ".join(list(intents_or_messages) + list(actions)).lower()
        domain_hits: dict[str, int] = {}
        for domain, keywords in DOMAIN_KEYWORDS.items():
            overlap = sum(1 for kw in keywords if kw in all_text)
            if overlap > 0:
                domain_hits[domain] = overlap
        return [d for d, _ in sorted(domain_hits.items(), key=lambda x: -x[1])][:5]

    # Single arg: could be chunks, messages, or intent strings
    arg = intents_or_messages
    if not arg:
        return []
    if isinstance(arg[0], SemanticChunk):
        # Chunk list
        domain_counts: dict[str, int] = {}
        for c in arg:
            domain_counts[c.domain] = domain_counts.get(c.domain, 0) + 1
        return [d for d, _ in sorted(domain_counts.items(), key=lambda x: -x[1])][:5]
    if isinstance(arg[0], dict):
        # Messages list
        chunks = extract_chunks(arg)
        domain_counts2: dict[str, int] = {}
        for c in chunks:
            domain_counts2[c.domain] = domain_counts2.get(c.domain, 0) + 1
        return [d for d, _ in sorted(domain_counts2.items(), key=lambda x: -x[1])][:5]
    # String list (legacy intents)
    all_text2 = " ".join(arg).lower()
    domain_hits2: dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        overlap = sum(1 for kw in keywords if kw in all_text2)
        if overlap > 0:
            domain_hits2[domain] = overlap
    return [d for d, _ in sorted(domain_hits2.items(), key=lambda x: -x[1])][:5]


# ---------------------------------------------------------------------------
# Session DB access
# ---------------------------------------------------------------------------

def _fetch_session(cur, session_id: str) -> dict | None:
    cur.execute("""
        SELECT id, source, model, started_at, message_count, title
        FROM sessions
        WHERE id = ?
    """, (session_id,))
    row = cur.fetchone()
    if not row:
        return None

    session = dict(row)
    cur.execute("""
        SELECT role, substr(content, 1, 300) as content_preview
        FROM messages
        WHERE session_id = ? AND role IN ('user', 'assistant')
        ORDER BY id DESC
        LIMIT 200
    """, (session["id"],))
    session["messages"] = [dict(m) for m in cur.fetchall()]
    return session


def get_session_by_id(db_path: str | Path, session_id: str) -> dict | None:
    if not os.path.exists(db_path) or not session_id:
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return _fetch_session(conn.cursor(), session_id)
    finally:
        conn.close()


def get_latest_session(db_path: str | Path) -> dict | None:
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, source, model, started_at, message_count, title
            FROM sessions
            WHERE source != 'cron' AND message_count > 0
            ORDER BY started_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            return None
        session = dict(row)
        cur.execute("""
            SELECT role, substr(content, 1, 300) as content_preview
            FROM messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY id DESC
            LIMIT 200
        """, (session["id"],))
        session["messages"] = [dict(m) for m in cur.fetchall()]
        return session
    finally:
        conn.close()


# Legacy compat — still used by session_handoff.py and tests
def extract_intents(messages: list[dict]) -> list[str]:
    """Extract user intents — legacy compat, returns chunk payloads for user messages."""
    chunks = extract_chunks(messages)
    return [c.payload for c in chunks if c.source_role == "user"][:MAX_USER_INTENTS]

def extract_actions(messages: list[dict]) -> list[str]:
    """Extract assistant actions — legacy compat."""
    chunks = extract_chunks(messages)
    return [c.payload for c in chunks if c.source_role == "assistant"][:MAX_ASSISTANT_ACTIONS]

def extract_reasoning(messages: list[dict]) -> list[str]:
    """Extract reasoning — legacy compat."""
    chunks = extract_chunks(messages)
    return [f"[{c.tag}] {c.payload}" for c in chunks if c.tag in ("decision", "bug", "fix")][:MAX_REASONING]


# ---------------------------------------------------------------------------
# Enrichment — add Conscio context
# ---------------------------------------------------------------------------

def _active_shard_value(engine) -> str:
    try:
        shard_engine = getattr(engine, "shard_engine", None)
        if shard_engine is not None and shard_engine.current is not None:
            return shard_engine.current.value
    except Exception as e:
        logger.debug("shard_engine.current.value failed: %s", e)
    return ""


def enrich_with_conscio(summary: SessionSummary, engine) -> SessionSummary:
    try:
        entities = engine.world.list_entities(limit=5)
        summary.world_model_entities = [
            f"{e['name']}:{e.get('state', '?')}" for e in entities
        ]
    except Exception as e:
        logger.debug("world.list_entities failed: %s", e)

    try:
        summary.active_goals = [g.description for g in engine.goals.active_goals()[:3]]
    except Exception as e:
        logger.debug("goals.active_goals failed: %s", e)

    try:
        summary.meta_confidence = engine.meta.average_confidence()
    except Exception as e:
        logger.debug("meta.average_confidence failed: %s", e)

    try:
        summary.stale_entities = engine.world.stale_entities()[:3]
    except Exception as e:
        logger.debug("world.stale_entities failed: %s", e)

    try:
        shard_val = _active_shard_value(engine)
        top_goal = summary.active_goals[0] if summary.active_goals else ""
        if shard_val and top_goal:
            summary.trajectory = f"{shard_val} → {top_goal}"
        elif shard_val or top_goal:
            summary.trajectory = shard_val or top_goal
    except Exception as e:
        logger.debug("trajectory enrichment failed: %s", e)

    try:
        rep = getattr(engine, "last_coherence", None)
        if rep is not None:
            summary.coherence = rep.score
            summary.coherence_note = rep.dominant.dimension if rep.dominant else ""
    except Exception as e:
        logger.debug("coherence read failed: %s", e)

    try:
        summary.voice = getattr(engine, "voice_preset", "")
    except Exception as e:
        logger.debug("voice_preset read failed: %s", e)

    try:
        prompts = getattr(engine, "last_self_prompts", None)
        summary.self_prompt = prompts[0].question if prompts else ""
    except Exception as e:
        logger.debug("last_self_prompts read failed: %s", e)

    try:
        rec = getattr(engine, "dream_recommended", None)
        summary.dream_recommended = rec.marker() if rec is not None else ""
    except Exception as e:
        logger.debug("dream_recommended.marker failed: %s", e)

    return summary


# ---------------------------------------------------------------------------
# Formatting — Heartbeat (semantic map, ultra-compact)
# ---------------------------------------------------------------------------

def format_heartbeat(summary: SessionSummary) -> str:
    """Semantic map heartbeat — chunks as tagged tokens, like embedding metadata.
    Goal: maximum info density / minimum context weight."""
    date_str = datetime.now().strftime('%Y-%m-%d')
    lines = [
        f"# ♥ {date_str}",
        f"`{summary.session_id[:16]}` {summary.model} {summary.message_count}msg",
        f"\"{summary.title[:50]}\"" if summary.title else "",
    ]

    # Trajectory / coherence / voice — single line each
    if summary.trajectory:
        lines.append(f"→ {summary.trajectory[:60]}")
    if summary.coherence is not None:
        coh_parts = [f"{summary.coherence:.2f}"]
        if summary.coherence_note:
            coh_parts.append(summary.coherence_note)
        if summary.voice:
            coh_parts.append(summary.voice)
        lines.append(f"⊙ {' '.join(coh_parts)}")
    elif summary.voice:
        lines.append(f"⊙ {summary.voice}")
    if summary.self_prompt:
        lines.append(f"? {summary.self_prompt[:60]}")
    if summary.dream_recommended:
        lines.append(f"☾ {summary.dream_recommended}")
    if summary.vibes:
        lines.append(f"⚡ {summary.vibes[:40]}")

    lines.append("")

    # Semantic chunks — the core innovation
    # Group by domain for scanability
    if summary.chunks:
        by_domain: dict[str, list[SemanticChunk]] = {}
        for c in summary.chunks:
            by_domain.setdefault(c.domain, []).append(c)
        for domain, dchunks in by_domain.items():
            for c in dchunks:
                marker = {"fix": "✓", "create": "+", "change": "~", "verify": "✔",
                          "decision": "◆", "bug": "✗", "state": "■", "config": "⚙",
                          "request": "▸", "info": "·"}.get(c.tag, "·")
                lines.append(f"{marker}{domain[:3]}|{c.payload}")
    else:
        # Legacy compat: show intents/actions if no chunks
        for i in summary.intents[:4]:
            lines.append(f"▸usr|{i[:50]}")
        for a in summary.actions[:3]:
            lines.append(f"◆her|{a[:50]}")

    # Conscio goals — compact
    if summary.active_goals:
        for g in summary.active_goals[:2]:
            lines.append(f"◎ {g[:60]}")
    if summary.stale_entities:
        lines.append(f"⚠ {','.join(summary.stale_entities[:2])}")

    lines.append(f"— {datetime.now(timezone.utc).strftime('%H:%M')}Z")

    content = "\n".join(lines)
    if len(content) > HB_MAX_CHARS:
        content = content[:HB_MAX_CHARS - 3] + "…"
    return content


# ---------------------------------------------------------------------------
# Formatting — Handoff (richer semantic map, still bounded)
# ---------------------------------------------------------------------------

def format_handoff(summary: SessionSummary) -> str:
    """Semantic map handoff — like heartbeat but with chunk detail + Conscio state.
    Each chunk is self-contained: tag + domain + payload = retrievable unit."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_str = datetime.now().strftime('%Y-%m-%d')

    lines = [
        f"# ◆ {date_str}",
        f"gen:{now} sid:{summary.session_id[:16]} mdl:{summary.model}",
        f"msg:{summary.message_count} \"{summary.title[:50]}\"",
        "",
    ]

    # Semantic chunks — full detail (domain context + tag + payload)
    if summary.chunks:
        by_domain: dict[str, list[SemanticChunk]] = {}
        for c in summary.chunks:
            by_domain.setdefault(c.domain, []).append(c)
        for domain, dchunks in by_domain.items():
            lines.append(f"## {domain}")
            for c in dchunks:
                role = "👤" if c.source_role == "user" else "🤖"
                lines.append(f" {role} [{c.tag}] {c.payload}")
            lines.append("")
    else:
        # Legacy compat: show intents/actions/reasoning if no chunks
        if summary.intents:
            lines.append("## intents")
            for i in summary.intents[:6]:
                lines.append(f" 👤 {i[:70]}")
            lines.append("")
        if summary.actions:
            lines.append("## actions")
            for a in summary.actions[:5]:
                lines.append(f" 🤖 {a[:70]}")
            lines.append("")
        if summary.reasoning:
            lines.append("## reasoning")
            for r in summary.reasoning[:4]:
                lines.append(f" ◆ {r[:70]}")
            lines.append("")

    # Conscio state — compact key:value
    if any([summary.world_model_entities, summary.active_goals,
            summary.stale_entities, summary.trajectory]):
        lines.append("## conscio")
        if summary.world_model_entities:
            lines.append(f"  world: {','.join(summary.world_model_entities[:4])}")
        if summary.active_goals:
            for g in summary.active_goals[:3]:
                lines.append(f"  goal: {g[:70]}")
        if summary.stale_entities:
            lines.append(f"  stale: {','.join(summary.stale_entities[:3])}")
        if summary.trajectory:
            lines.append(f"  traj: {summary.trajectory[:70]}")
        if summary.coherence is not None:
            lines.append(f" coh: {summary.coherence:.2f} {summary.coherence_note}")
        if summary.voice:
            lines.append(f" voice: {summary.voice}")
        if summary.vibes:
            lines.append(f" vibes: {summary.vibes}")
        if summary.self_prompt:
            lines.append(f" **Self-prompt:** {summary.self_prompt[:70]}")
        if summary.dream_recommended:
            lines.append(f" ☾ dream: {summary.dream_recommended[:50]}")
        if summary.identity_anchor:
            lines.append(f" id: {summary.identity_anchor[:50]}")
        lines.append("")

    # Recovery hints — for next session
    lines.extend([
        "## next",
        "  heartbeat→context auto | skill_view antes de agir | session_search p/ recall",
        "",
        f"— {datetime.now(timezone.utc).strftime('%H:%M')}Z",
    ])

    content = "\n".join(lines)
    if len(content) > HO_MAX_CHARS:
        content = content[:HO_MAX_CHARS - 3] + "…"
    return content


# ---------------------------------------------------------------------------
# Main entry — record_session_lifecycle()
# ---------------------------------------------------------------------------

def record_session_lifecycle(
    event_type: str,
    context: dict,
    engine=None,
    session_db: Path | None = None,
    handoff_dir: Path | None | Any = _UNSET,
) -> SessionSummary | None:
    if event_type not in ("session:end", "session:reset"):
        return None

    _session_db = session_db if session_db is not None else SESSION_DB

    if handoff_dir is _UNSET:
        _handoff_dir: Path | None = HANDOFF_DIR
    else:
        _handoff_dir = handoff_dir

    # 1. Extract session
    context_sid = context.get("session_id") if context else None
    if context_sid and context_sid != "cron":
        session = get_session_by_id(_session_db, context_sid)
    else:
        session = get_latest_session(_session_db)
    if session is None:
        return None

    messages = session.get("messages", [])
    if not messages:
        return None

    # 2. Build semantic summary
    chunks = extract_chunks(messages)
    summary = SessionSummary(
        session_id=session.get("id", "?"),
        model=session.get("model", "?"),
        started_at=session.get("started_at", "unknown"),
        message_count=session.get("message_count", 0),
        title=session.get("title", "N/A"),
        chunks=chunks,
        topics=infer_topics(chunks),
    )

    # 3. Enrich via Conscio
    own_engine = engine is None
    if own_engine:
        from .engine import ConsciousnessEngine
        engine = ConsciousnessEngine(model_name=summary.model or "glm-5.1")

    heartbeat = ""
    handoff = ""

    try:
        enrich_with_conscio(summary, engine)

        # Emit session event — graceful if event_bus missing
        if hasattr(engine, "event_bus"):
            engine.event_bus.emit(
                type="session",
                category="session",
                data={
                    "event": event_type,
                    "session_id": summary.session_id,
                    "model": summary.model,
                    "message_count": summary.message_count,
                    "topics": summary.topics,
                    "chunks_count": len(summary.chunks),
                    "meta_confidence": summary.meta_confidence,
                },
            )

        heartbeat = format_heartbeat(summary)
        if hasattr(engine, "output_filter") and engine.output_filter:
            heartbeat = engine.output_filter.apply(heartbeat)

        # Index heartbeat — graceful if content_store missing
        if hasattr(engine, "content_store"):
            engine.content_store.index(
                label=f"heartbeat_{datetime.utcnow().strftime('%Y%m%d_%H%M')}",
                content=heartbeat,
                category="session",
                session_id=summary.session_id,
            )

        handoff = format_handoff(summary)
        if hasattr(engine, "output_filter") and engine.output_filter:
            handoff = engine.output_filter.apply(handoff)

        if hasattr(engine, "content_store"):
            engine.content_store.index(
                label=f"handoff_{datetime.utcnow().strftime('%Y%m%d_%H%M')}",
                content=handoff,
                category="session",
                session_id=summary.session_id,
            )

        # Reflection — graceful if reflect missing
        if hasattr(engine, "reflect"):
            world_state = (
                f"Session {event_type}: {summary.message_count} msg, "
                f"topics: {','.join(summary.topics) or 'none'}, "
                f"chunks: {len(summary.chunks)}, "
                f"conf: {summary.meta_confidence:.2f}"
            )
            anomalies = []
            if summary.stale_entities:
                anomalies.append(f"Stale: {','.join(summary.stale_entities)}")
            engine.reflect(
                world_state=world_state,
                recent_events=[f"{c.tag}:{c.payload[:50]}" for c in summary.chunks[:3]],
                confidence=summary.meta_confidence or 0.5,
                anomalies=anomalies,
            )

    except Exception as e:
        logger.debug("session enrichment/indexing failed: %s", e)

    # Dream — always attempt, outside the enrichment try block
    try:
        engine.dream()
    except Exception as e:
        logger.debug("engine.dream() failed: %s", e)

    finally:
        if own_engine:
            engine.close()

    # 4. Persist
    if _handoff_dir is not None:
        _handoff_dir.mkdir(parents=True, exist_ok=True)
        (_handoff_dir / "_session_handoff.md").write_text(handoff, encoding="utf-8")
        (_handoff_dir / "_latest_heartbeat.md").write_text(heartbeat, encoding="utf-8")

    return summary


class SessionLifecycle:
    def __init__(self, engine=None):
        self.engine = engine
        self.on_session_start = None
        self.on_session_end = None
        self.on_session_reset = None

    def handle_event(self, event_type: str, context: dict):
        if event_type == "session:start":
            if self.on_session_start:
                return self.on_session_start(event_type, context)
        elif event_type == "session:end":
            if self.on_session_end:
                self.on_session_end(event_type, context)
        elif event_type == "session:reset":
            if self.on_session_reset:
                self.on_session_reset(event_type, context)

    def record_session(self, event_type: str, context: dict) -> SessionSummary | None:
        return record_session_lifecycle(
            event_type, context,
            engine=self.engine,
            session_db=self.session_db,
            handoff_dir=self.handoff_dir,
        )
