#!/usr/bin/env python3
"""
session_handoff.py — Gera _session_handoff.md e _latest_heartbeat.md
ao final de cada sessão, garantindo continuidade entre resets.

Uso: python3 session_handoff.py [--session-dir DIR] [--diary-dir DIR]

Lê o state.db do Hermes para extrair o resumo da última sessão ativa.
Escreve em ~/mempalace/diary/ por padrão.

Design:
  - Zero dependências externas (stdlib + sqlite3)
  - Idempotente (pode rodar várias vezes)
  - Compacto: handoff < 2KB, heartbeat < 1KB
  - Evita poluir com context-compaction artifacts
"""

import sqlite3
import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_HERMES_HOME = os.path.expanduser("~/.hermes")
DEFAULT_DIARY_DIR = os.path.expanduser("~/mempalace/diary")

# Patterns to strip from handoff (context compaction noise)
NOISE_PATTERNS = [
    r"\[CONTEXT COMPACTION.*?\]",
    r"\[Your active task list was preserved.*?\]",
    r"\[IMPORTANT: Background process.*?\]",
    r"\[System note: Your previous turn was interrupted.*?\]",
    r"\[System note:.*?\]",
]


def get_db_path(hermes_home: str) -> str:
    """Resolve o caminho do state.db"""
    return os.path.join(hermes_home, "state.db")


def get_latest_session(db_path: str) -> dict | None:
    """Extrai a sessão mais recente do state.db"""
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # Get latest session that has actual messages (skip empty/reset/cron sessions)
        cur.execute("""
            SELECT id, source, model, started_at, message_count,
                   input_tokens, output_tokens, title
            FROM sessions
            WHERE message_count > 0 AND source != 'cron'
            ORDER BY started_at DESC
            LIMIT 1
        """)
        session = cur.fetchone()
        if not session:
            return None

        session_dict = dict(session)

        # Get user and assistant messages only (skip tool calls which dominate the count)
        cur.execute("""
            SELECT role, substr(content, 1, 200) as content_preview
            FROM messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY id DESC
            LIMIT 200
        """, (session_dict['id'],))
        session_dict['recent_messages'] = [dict(m) for m in cur.fetchall()]

        return session_dict
    finally:
        conn.close()


def strip_noise(text: str) -> str:
    """Remove artefatos de context compaction"""
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.DOTALL)
    return text.strip()


def extract_user_intents(messages: list[dict]) -> list[str]:
    """Extrai as últimas intenções reais do usuário (sem noise)"""
    intents = []
    for msg in messages:
        if msg.get('role') != 'user':
            continue
        content = msg.get('content_preview', '') or ''
        # Skip system-injected messages entirely (don't strip, just skip)
        if any(content.startswith(prefix) for prefix in [
            '[CONTEXT COMPACTION', '[Your active task',
            '[IMPORTANT: Background', '[System note:',
            '[IMPORTANT: You are running as a scheduled cron'
        ]):
            continue
        content = strip_noise(content)
        if not content or len(content) < 5:
            continue
        intents.append(content)
    return intents[-8:]  # Last 8 real user messages


def extract_assistant_actions(messages: list[dict]) -> list[str]:
    """Extrai as últimas ações do assistente"""
    actions = []
    for msg in messages:
        if msg.get('role') != 'assistant':
            continue
        content = msg.get('content_preview', '') or ''
        # Skip compaction artifacts
        if any(content.startswith(prefix) for prefix in [
            '[CONTEXT COMPACTION', '[Your active task',
            '[IMPORTANT: Background', '[System note:',
            'Earlier turns were compacted',
        ]):
            continue
        content = strip_noise(content)
        if not content or len(content) < 10:
            continue
        # Truncate to first meaningful line
        first_line = content.split('\n')[0][:100]
        actions.append(first_line)
    return actions[-5:]


def generate_handoff(session: dict) -> str:
    """Gera o conteúdo do _session_handoff.md"""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    started = datetime.fromtimestamp(session['started_at'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    user_intents = extract_user_intents(session.get('recent_messages', []))
    assistant_actions = extract_assistant_actions(session.get('recent_messages', []))

    lines = [
        f"# 🔄 Session Handoff — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**Gerado em:** {now}",
        f"**Session ID:** `{session['id']}`",
        f"**Modelo:** {session.get('model', 'unknown')}",
        f"**Iniciada em:** {started}",
        f"**Mensagens:** {session.get('message_count', 0)}",
        f"**Título:** {session.get('title', 'sem título')}",
        "",
        "---",
        "",
        "## 📋 Últimos Pedidos do Senhor",
        "",
    ]

    for i, intent in enumerate(user_intents, 1):
        lines.append(f"**{i}.** {intent}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 🤖 Últimas Ações do Hermet",
        "",
    ])

    for i, action in enumerate(assistant_actions, 1):
        lines.append(f"**{i}.** {action}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 🔧 Instruções para Próxima Sessão",
        "",
        "1. Leia os pedidos acima e pergunte ao Senhor se quer continuar algum",
        "2. Carregue skills relevantes com `skill_view` antes de agir",
        "3. Verifique MemPalace com `mempalace search` para contexto adicional",
        "4. Se o Senhor perguntar \"lembra?\", responda com o resumo deste handoff",
        "",
    ])

    return '\n'.join(lines)


def generate_heartbeat(session: dict) -> str:
    """Gera o conteúdo do _latest_heartbeat.md"""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    user_intents = extract_user_intents(session.get('recent_messages', []))

    lines = [
        f"# Heartbeat — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**Session:** `{session['id']}`",
        f"**Model:** {session.get('model', 'unknown')}",
        f"**Messages:** {session.get('message_count', 0)}",
        "",
        "## Últimos pedidos do Senhor",
    ]

    for i, intent in enumerate(user_intents[-5:], 1):
        lines.append(f"{i}. {intent}")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Generate session handoff documents')
    parser.add_argument('--hermes-home', default=DEFAULT_HERMES_HOME,
                        help='Hermes home directory')
    parser.add_argument('--diary-dir', default=DEFAULT_DIARY_DIR,
                        help='MemPalace diary directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print to stdout instead of writing files')
    args = parser.parse_args()

    db_path = get_db_path(args.hermes_home)
    session = get_latest_session(db_path)

    if not session:
        print("No session found in state.db")
        return

    handoff = generate_handoff(session)
    heartbeat = generate_heartbeat(session)

    if args.dry_run:
        print("=== HANDOFF ===")
        print(handoff)
        print("\n=== HEARTBEAT ===")
        print(heartbeat)
        return

    # Ensure diary dir exists
    os.makedirs(args.diary_dir, exist_ok=True)

    # Write handoff
    handoff_path = os.path.join(args.diary_dir, '_session_handoff.md')
    with open(handoff_path, 'w') as f:
        f.write(handoff)

    # Write heartbeat (both the _latest and dated copy)
    heartbeat_path = os.path.join(args.diary_dir, '_latest_heartbeat.md')
    today = datetime.now().strftime('%Y-%m-%d')
    dated_path = os.path.join(args.diary_dir, f'{today}_heartbeat-{datetime.now().strftime("%H%M")}.md')

    with open(heartbeat_path, 'w') as f:
        f.write(heartbeat)

    with open(dated_path, 'w') as f:
        f.write(heartbeat)

    print(f"Handoff: {handoff_path} ({len(handoff)} bytes)")
    print(f"Heartbeat: {heartbeat_path} ({len(heartbeat)} bytes)")
    print(f"Dated: {dated_path}")


if __name__ == '__main__':
    main()
