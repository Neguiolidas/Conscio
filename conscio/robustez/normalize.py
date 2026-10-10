"""Normalização defensiva de conectores para registros estruturados.

Origem:
- OpenBot: server/src/memory/ingestion.ts:13-100
Stdlib apenas.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_INGESTION_BYTES = 256 * 1024  # 256 KiB
MAX_RECORDS_CAP = 50
_WRAPPER_KEYS = ("items", "files", "results", "data")


def normalize_records(
    text: str,
    default_source: str = "unknown",
    stats: dict | None = None,
) -> list[dict[str, str]]:
    """Normaliza entrada de conectores em registros estruturados com sha256 digest.

    Rejeita cargas > 256 KiB. Texto vazio ou só espaços: nenhum registro.
    Desembrulha chaves conhecidas ('items', 'files', 'results', 'data'): a
    primeira que contém uma lista é a carga, e lista vazia dá zero registros.
    Dict sem chave conhecida vira um registro; texto que não é lista nem dict
    (texto plano ou escalar JSON como "42") vira um registro com o texto.
    Aplica cap máximo de 50 registros; se houver truncamento e ``stats`` for
    fornecido, ele recebe {'total': N, 'returned': M, 'truncated': bool} —
    truncamento silencioso vira truncamento visível.
    Não deduplica: itens com o mesmo conteúdo viram registros separados, com o
    mesmo ``digest``. Deduplicar é decisão do consumidor, que usa o ``digest``.
    """
    raw_bytes = text.encode("utf-8")
    if len(raw_bytes) > MAX_INGESTION_BYTES:
        raise ValueError(f"Payload size ({len(raw_bytes)} bytes) exceeds 256 KiB limit")
    if not text.strip():
        return []

    parsed_json: Any = None
    try:
        parsed_json = json.loads(text)
    except Exception:
        parsed_json = None

    raw_list: list[Any]
    if isinstance(parsed_json, list):
        raw_list = parsed_json
    elif isinstance(parsed_json, dict):
        unwrapped = next((parsed_json[k] for k in _WRAPPER_KEYS
                          if isinstance(parsed_json.get(k), list)), None)
        raw_list = [parsed_json] if unwrapped is None else unwrapped
    else:
        # Texto plano, ou escalar JSON ("42", "true", "null"): o texto é o registro
        raw_list = [{"content": text.strip()}]

    records: list[dict[str, str]] = []

    total = len(raw_list)
    for item in raw_list[:MAX_RECORDS_CAP]:
        if isinstance(item, dict):
            content = str(item.get("content") or item.get("text") or item.get("body") or json.dumps(item))
            ext_id = str(item.get("external_id") or item.get("id") or "")
            prov = str(item.get("provenance") or item.get("source") or default_source)
        else:
            content = str(item)
            ext_id = ""
            prov = default_source

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        records.append({
            "external_id": ext_id,
            "content": content,
            "provenance": prov,
            "digest": digest,
        })

    if stats is not None:
        stats.update({
            "total": total,
            "returned": len(records),
            "truncated": total > len(records),
        })

    return records
