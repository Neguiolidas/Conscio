"""Reparo e saneamento de histórico de conversação com tool calls pendentes ou órfãos.

Origem:
- OpenBot: server/src/agents/history-sanitize.ts
Nasceu de incidentes reais de LLM onde tool calls incompletos geravam falha permanente em chamadas subsequentes.
Stdlib apenas.
"""

from __future__ import annotations

from typing import Any


def sanitize_history(
    history: list[dict[str, Any]],
    answered_elsewhere: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Função pura para sanear histórico de mensagens.
    
    Remove:
    - Chamadas de ferramentas de mensagens assistente sem resposta correspondente no turno.
    - Mensagens de ferramentas órfãs sem chamada prévia do assistente.
    Boundary é a próxima mensagem 'user'.
    Não muta a entrada; histórico saudável passa intocado.
    """
    if not history:
        return []

    # Fast check: are there tool calls or tool messages?
    has_tools = any(
        m.get("role") == "tool" or bool(m.get("tool_calls"))
        for m in history
    )
    if not has_tools:
        return history

    sanitized: list[dict[str, Any]] = []
    
    # Process turn by turn divided by user message boundaries
    # Each turn consists of: [optional user msg, assistant msgs with tool calls, tool responses]
    turn_messages: list[dict[str, Any]] = []

    def flush_turn(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return []

        # Find all answered tool_call_ids in this turn
        tool_ids_answered = {
            m.get("tool_call_id")
            for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        tool_ids_answered.update(answered_elsewhere)

        # Find all declared tool_call_ids in assistant messages in this turn
        declared_call_ids: set[str] = set()
        for m in messages:
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in m.get("tool_calls", []):
                    if isinstance(tc, dict) and tc.get("id"):
                        declared_call_ids.add(tc["id"])

        cleaned_turn: list[dict[str, Any]] = []

        for m in messages:
            role = m.get("role")
            if role == "tool":
                call_id = m.get("tool_call_id")
                # Drop orphan tool results
                if call_id and call_id not in declared_call_ids and call_id not in answered_elsewhere:
                    continue
                cleaned_turn.append(m)
            elif role == "assistant" and "tool_calls" in m:
                raw_calls = m.get("tool_calls", [])
                valid_calls = [
                    tc for tc in raw_calls
                    if isinstance(tc, dict) and tc.get("id") in tool_ids_answered
                ]
                if valid_calls:
                    # Keep assistant message with filtered tool_calls
                    new_m = dict(m)
                    new_m["tool_calls"] = valid_calls
                    cleaned_turn.append(new_m)
                elif m.get("content"):
                    # Had text content, strip empty tool_calls
                    new_m = dict(m)
                    new_m.pop("tool_calls", None)
                    cleaned_turn.append(new_m)
                # Else: assistant message had only tool_calls and all were dropped -> drop message
            else:
                cleaned_turn.append(m)

        return cleaned_turn

    for msg in history:
        if msg.get("role") == "user" and turn_messages:
            sanitized.extend(flush_turn(turn_messages))
            turn_messages = [msg]
        else:
            turn_messages.append(msg)

    if turn_messages:
        sanitized.extend(flush_turn(turn_messages))

    return sanitized
