"""
SessionLifecycle — Bridge between Conscio Engine and Hermes session events.

Handles:
1. Recording session end/reset in Conscio (EventBus + ContentStore)
2. Enriching heartbeat with Conscio state (world model, active goals, anomalies)
3. Persisting heartbeat content to ContentStore for future semantic search
4. Feeding session data into the reflection loop

Called by the conscio-handoff hook (handler.py) on session:end / session:reset.
"""

from __future__ import annotations

import sqlite3
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
SESSION_DB = HERMES_HOME / "state.db"
MEMPALACE_DIR = Path.home() / "mempalace" / "diary"
HANDOFF_PATH = MEMPALACE_DIR / "_session_handoff.md"
HEARTBEAT_PATH = MEMPALACE_DIR / "_latest_heartbeat.md"

# Patterns to skip (system-injected messages, not real user input)
SKIP_PREFIXES = [
    "[CONTEXT COMPACTION",
    "[Your active task",
    "[IMPORTANT: Background",
    "[System note:",
    "[IMPORTANT: You are running as a scheduled cron",
    "[HEARTBEAT — Contexto da sessão anterior",
    "[FIM DO HEARTBEAT",
]

# Noise patterns to strip from content
NOISE_PATTERNS = [
    r"\[CONTEXT COMPACTION[^\]]*\]",
    r"\[Your active task list was preserved[^\]]*\]",
    r"\[IMPORTANT: Background[^\]]*\]",
    r"\[System note:[^\]]*\]",
    r"\[HEARTBEAT[^\]]*\]",
    r"\[FIM DO HEARTBEAT[^\]]*\]",
]

MAX_USER_INTENTS = 6
MAX_ASSISTANT_ACTIONS = 5
MAX_REASONING = 4

HB_MAX_INTENTS = 4
HB_MAX_ACTIONS = 3
HB_MAX_TOPICS = 5
HB_MAX_CHARS = 1400


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionSummary:
    """Extracted summary from a dying Hermes session."""
    session_id: str = ""
    model: str = ""
    started_at: str = ""
    message_count: int = 0
    title: str = ""
    intents: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    # Enriched from Conscio (optional — filled by engine)
    world_model_entities: list[str] = field(default_factory=list)
    active_goals: list[str] = field(default_factory=list)
    meta_confidence: float = 0.0
    stale_entities: list[str] = field(default_factory=list)

    # Trajectory (v0.5) — code-owned `trajectory`; LLM-only `vibes`/`identity_anchor`
    trajectory: str = ""        # where the agent is heading (overwritten by enrich)
    vibes: str = ""             # emotional texture — LLM-authored only
    identity_anchor: str = ""   # processing style — LLM-authored only


# ---------------------------------------------------------------------------
# Helpers — noise filtering
# ---------------------------------------------------------------------------

def strip_noise(text: str) -> str:
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.DOTALL)
    return text.strip()


def is_noise(content: str) -> bool:
    return any(content.startswith(prefix) for prefix in SKIP_PREFIXES)


# ---------------------------------------------------------------------------
# Extraction — from state.db
# ---------------------------------------------------------------------------

def get_latest_session(db_path: str | Path) -> dict | None:
    """Get the most recent non-cron session from state.db."""
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


def extract_intents(messages: list[dict]) -> list[str]:
    """Extract real user intents (skip noise, compaction artifacts)."""
    intents = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content_preview", "") or ""
        if is_noise(content):
            continue
        content = strip_noise(content)
        if not content or len(content) < 5:
            continue
        intents.append(content)
    return intents[:MAX_USER_INTENTS]


def extract_actions(messages: list[dict]) -> list[str]:
    """Extract key assistant actions (first meaningful line of each)."""
    actions = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content_preview", "") or ""
        if is_noise(content) or content.startswith("Earlier turns"):
            continue
        content = strip_noise(content)
        if not content or len(content) < 10:
            continue
        first_line = content.split("\n")[0].strip()[:120]
        actions.append(first_line)
    return actions[:MAX_ASSISTANT_ACTIONS]


def extract_reasoning(messages: list[dict]) -> list[str]:
    """Extract reasoning snippets — why decisions were made."""
    reasoning_patterns = [
        r"(?i)(bug|problema|erro|issue|pitfall|racioc[íi]nio|decis[ãa]o|motivo|raz[ãa]o|porque|why)",
        r"(?i)(corrigir|resolver|preciso verificar|vou investigar|descobri que)",
        r"(?i)(filtrar|filtro|pattern|padr[ãa]o|hook|cron|session)",
    ]
    reasoning = []
    for msg in messages:
        content = msg.get("content_preview", "") or ""
        if is_noise(content):
            continue
        content = strip_noise(content)
        if not content or len(content) < 20:
            continue
        if any(re.search(p, content) for p in reasoning_patterns):
            snippet = content.split("\n")[0].strip()[:150]
            reasoning.append(f"[{msg.get('role', '?')}] {snippet}")
    return reasoning[:MAX_REASONING]


def infer_topics(intents: list[str], actions: list[str]) -> list[str]:
    """Infer conversation topics from intents and actions."""
    topic_keywords = {
        "trading": ["trading", "bot", "okx", "swap", "order", "pnl", "usdt", "position"],
        "conscio": ["conscio", "consciousness", "handoff", "heartbeat", "session_reset"],
        "orion": ["orion", "voice", "assistant", "android"],
        "infra": ["server", "gateway", "deploy", "docker", "nginx", "systemd", "ssh"],
        "debug": ["bug", "error", "fix", "debug", "traceback", "fail"],
        "code": ["refactor", "code", "test", "pipeline", "function", "class"],
        "hermes": ["hermes", "skill", "hook", "cron", "agent", "model", "provider"],
    }

    all_text = " ".join(intents + actions).lower()
    topics = []
    for topic, keywords in topic_keywords.items():
        if any(kw in all_text for kw in keywords):
            topics.append(topic)
    return topics[:HB_MAX_TOPICS]


# ---------------------------------------------------------------------------
# Enrichment — add Conscio context
# ---------------------------------------------------------------------------

def _active_shard_value(engine) -> str:
    """The engine's current cognitive shard value, or '' if unset/unavailable."""
    try:
        shard_engine = getattr(engine, "shard_engine", None)
        if shard_engine is not None and shard_engine.current is not None:
            return shard_engine.current.value
    except Exception:
        pass
    return ""


def enrich_with_conscio(summary: SessionSummary, engine) -> SessionSummary:
    """
    Enrich session summary with Conscio engine state.

    Args:
        summary: The extracted session summary
        engine: A ConsciousnessEngine instance (must be open/not closed)

    Returns:
        The enriched summary (mutated in-place)
    """
    # World model — get top entities by relevance
    try:
        entities = engine.world.list_entities(limit=5)
        summary.world_model_entities = [
            f"{e['name']}:{e.get('state', '?')}" for e in entities
        ]
    except Exception:
        pass

    # Active goals
    try:
        summary.active_goals = [g.description for g in engine.goals.active_goals()[:3]]
    except Exception:
        pass

    # Meta-cognition
    try:
        summary.meta_confidence = engine.meta.average_confidence()
    except Exception:
        pass

    # Stale entities (need attention)
    try:
        summary.stale_entities = engine.world.stale_entities()[:3]
    except Exception:
        pass

    # Trajectory — code-owned; always overwrite (more current than last heartbeat).
    # vibes + identity_anchor are LLM-only and are never touched here.
    try:
        shard_val = _active_shard_value(engine)
        top_goal = summary.active_goals[0] if summary.active_goals else ""
        if shard_val and top_goal:
            summary.trajectory = f"{shard_val} → {top_goal}"
        elif shard_val or top_goal:
            summary.trajectory = shard_val or top_goal
    except Exception:
        pass

    return summary


# ---------------------------------------------------------------------------
# Formatting — Handoff (richer, preserved for manual reference)
# ---------------------------------------------------------------------------

def format_handoff(summary: SessionSummary) -> str:
    """Format the handoff as a compact document."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# 🔄 Session Handoff — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**Gerado em:** {now}",
        f"**Session ID:** `{summary.session_id}`",
        f"**Modelo:** {summary.model}",
        f"**Iniciada em:** {summary.started_at}",
        f"**Mensagens:** {summary.message_count}",
        f"**Título:** {summary.title}",
        "",
        "---",
        "",
    ]

    if summary.intents:
        lines.append("## 📋 Últimos Pedidos do Senhor")
        lines.append("")
        for i, intent in enumerate(summary.intents, 1):
            lines.append(f"**{i}.** {intent}")
        lines.append("")

    if summary.actions:
        lines.append("## 🤖 Últimas Ações do Hermet")
        lines.append("")
        for i, action in enumerate(summary.actions, 1):
            lines.append(f"**{i}.** {action}")
        lines.append("")

    if summary.reasoning:
        lines.append("## 🧠 Raciocínio e Decisões")
        lines.append("")
        for r in summary.reasoning:
            lines.append(f"- {r}")
        lines.append("")

    # Conscio enrichment section
    if summary.world_model_entities or summary.active_goals:
        lines.append("## 🧬 Estado Conscio")
        lines.append("")
        if summary.world_model_entities:
            lines.append(f"**Mundo:** {', '.join(summary.world_model_entities)}")
        if summary.active_goals:
            lines.append(f"**Metas ativas:** {'; '.join(summary.active_goals)}")
        if summary.meta_confidence > 0:
            lines.append(f"**Confiança média:** {summary.meta_confidence:.2f}")
        if summary.stale_entities:
            lines.append(f"**Entidades stale:** {', '.join(summary.stale_entities)}")
        if summary.trajectory:
            lines.append(f"**Trajetória:** {summary.trajectory}")
        if summary.vibes:
            lines.append(f"**Vibe:** {summary.vibes}")
        if summary.identity_anchor:
            lines.append(f"**Âncora de identidade:** {summary.identity_anchor}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 🔧 Para a Próxima Sessão",
        "",
        "1. O heartbeat já foi injetado no contexto — use-o diretamente",
        "2. Carregue skills relevantes com `skill_view` antes de agir",
        "3. Verifique MemPalace com `mempalace search` para contexto adicional",
        "4. Se o Senhor perguntar \"lembra?\", responda com este resumo",
        "5. **Filtre cron sessions** ao buscar no session DB (`source != 'cron'`)",
        "",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting — Heartbeat (compact, auto-injected on new session)
# ---------------------------------------------------------------------------

def format_heartbeat(summary: SessionSummary) -> str:
    """Format a compact daily heartbeat. Always < HB_MAX_CHARS."""
    lines = [
        f"# ♥ Heartbeat — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**Sessão:** `{summary.session_id[:21]}`",
        f"**Modelo:** {summary.model}",
        f"**Mensagens:** {summary.message_count}",
        f"**Título:** {summary.title}",
    ]

    if summary.trajectory:
        lines.append(f"**Trajetória:** {summary.trajectory[:80]}")
    if summary.vibes:
        lines.append(f"**Vibe:** {summary.vibes[:80]}")
    if summary.identity_anchor:
        lines.append(f"**Âncora:** {summary.identity_anchor[:80]}")

    if summary.topics:
        lines.append(f"**Tópicos:** {', '.join(summary.topics)}")

    if summary.meta_confidence > 0:
        lines.append(f"**Conscio confiança:** {summary.meta_confidence:.1f}")

    lines.extend(["", "---", ""])

    if summary.intents:
        lines.append("## 📋 Pedidos do Senhor")
        lines.append("")
        for i, intent in enumerate(summary.intents[:HB_MAX_INTENTS], 1):
            # Compact: truncate for heartbeat
            short = intent[:120] + "..." if len(intent) > 120 else intent
            lines.append(f"{i}. {short}")
        lines.append("")

    if summary.actions:
        lines.append("## 🤖 Ações do Hermet")
        lines.append("")
        for i, action in enumerate(summary.actions[:HB_MAX_ACTIONS], 1):
            short = action[:100] + "..." if len(action) > 100 else action
            lines.append(f"{i}. {short}")
        lines.append("")

    # Conscio enrichment — compact (only top goals)
    if summary.active_goals:
        lines.append("## 🎯 Metas Conscio")
        lines.append("")
        for g in summary.active_goals[:2]:
            lines.append(f"- {g[:80]}")
        lines.append("")

    if summary.stale_entities:
        lines.append(f"⚠️ Stale: {', '.join(summary.stale_entities[:3])}")
        lines.append("")

    lines.extend([
        "---",
        "",
        f"*Gerado: {datetime.now(timezone.utc).strftime('%H:%M')} UTC*",
    ])

    content = "\n".join(lines)

    # Hard truncate if somehow over limit
    if len(content) > HB_MAX_CHARS:
        content = content[:HB_MAX_CHARS - 3] + "..."

    return content


# ---------------------------------------------------------------------------
# Main entry — record_session_lifecycle()
# ---------------------------------------------------------------------------

def record_session_lifecycle(
    event_type: str,
    context: dict,
    engine=None,
) -> SessionSummary | None:
    """
    Process a session lifecycle event through Conscio.

    This is the main integration point. Called by the hook handler on
    session:end / session:reset.

    Pipeline:
    1. Extract session summary from Hermes state.db
    2. Enrich with Conscio state (if engine provided)
    3. Emit session event to Conscio EventBus
    4. Index heartbeat into Conscio ContentStore (searchable)
    5. Run post-session reflection on Conscio engine
    6. Write heartbeat + handoff to disk

    Args:
        event_type: "session:end" or "session:reset"
        context: Hook context dict (platform, user_id, session_key, session_id)
        engine: Optional ConsciousnessEngine instance. If None, creates a temp one.

    Returns:
        SessionSummary if successful, None if no data.
    """
    if event_type not in ("session:end", "session:reset"):
        return None

    # 1. Extract session from state.db
    session = get_latest_session(SESSION_DB)
    if session is None:
        return None

    messages = session.get("messages", [])
    if not messages:
        return None

    # 2. Build summary
    summary = SessionSummary(
        session_id=session.get("id", "?"),
        model=session.get("model", "?"),
        started_at=session.get("started_at", "unknown"),
        message_count=session.get("message_count", 0),
        title=session.get("title", "N/A"),
        intents=extract_intents(messages),
        actions=extract_actions(messages),
        reasoning=extract_reasoning(messages),
        topics=infer_topics(extract_intents(messages), extract_actions(messages)),
    )

    # 3. Enrich + emit via Conscio engine
    own_engine = engine is None
    if own_engine:
        from .engine import ConsciousnessEngine
        engine = ConsciousnessEngine(model_name=summary.model or "glm-5.1")

    try:
        # Enrich summary with Conscio state
        enrich_with_conscio(summary, engine)

        # Emit session event to EventBus
        engine.event_bus.emit(
            type="session",
            category="session",
            data={
                "event": event_type,
                "session_id": summary.session_id,
                "model": summary.model,
                "message_count": summary.message_count,
                "topics": summary.topics,
                "intents_count": len(summary.intents),
                "actions_count": len(summary.actions),
                "meta_confidence": summary.meta_confidence,
            },
        )

        # Index heartbeat into ContentStore (searchable via FTS5)
        heartbeat = format_heartbeat(summary)
        engine.content_store.index(
            label=f"heartbeat_{datetime.utcnow().strftime('%Y%m%d_%H%M')}",
            content=heartbeat,
            category="session",
            session_id=summary.session_id,
        )

        # Index handoff too (richer, for semantic search)
        handoff = format_handoff(summary)
        engine.content_store.index(
            label=f"handoff_{datetime.utcnow().strftime('%Y%m%d_%H%M')}",
            content=handoff,
            category="session",
            session_id=summary.session_id,
        )

        # Run post-session reflection on Conscio
        world_state = (
            f"Session {event_type}: {summary.message_count} messages, "
            f"topics: {', '.join(summary.topics) or 'none'}, "
            f"confidence: {summary.meta_confidence:.2f}"
        )
        anomalies = []
        if summary.stale_entities:
            anomalies.append(f"Stale world entities: {', '.join(summary.stale_entities)}")

        engine.reflect(
            world_state=world_state,
            recent_events=[f"Session {event_type}: {a[:80]}" for a in summary.actions[:3]],
            confidence=summary.meta_confidence or 0.5,
            anomalies=anomalies,
        )

        # Mitosis → Dream: consolidate the DB now that the session is captured.
        # Best-effort: the handoff is already recorded; a dream failure must
        # not prevent persistence below.
        try:
            engine.dream()
        except Exception:
            pass

    finally:
        if own_engine:
            engine.close()

    # 4. Write to disk
    MEMPALACE_DIR.mkdir(parents=True, exist_ok=True)
    HANDOFF_PATH.write_text(handoff, encoding="utf-8")
    HEARTBEAT_PATH.write_text(heartbeat, encoding="utf-8")

    return summary
