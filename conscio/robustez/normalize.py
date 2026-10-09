"""Normalização defensiva de conectores para registros estruturados com deduplicação.

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


def normalize_records(
    text: str,
    default_source: str = "unknown",
) -> list[dict[str, str]]:
    """Normaliza entrada de conectores em registros estruturados com sha256 digest.
    
    Rejeita cargas > 256 KiB.
    Desembrulha chaves conhecidas ('items', 'files', 'results').
    Aplica cap máximo de 50 registros.
    """
    if not text:
        return []

    raw_bytes = text.encode("utf-8")
    if len(raw_bytes) > MAX_INGESTION_BYTES:
        raise ValueError(f"Payload size ({len(raw_bytes)} bytes) exceeds 256 KiB limit")

    parsed_json: Any = None
    try:
        parsed_json = json.loads(text)
    except Exception:
        parsed_json = None

    raw_list: list[Any] = []
    if parsed_json is not None:
        if isinstance(parsed_json, list):
            raw_list = parsed_json
        elif isinstance(parsed_json, dict):
            # Tenta desembrulhar
            for key in ("items", "files", "results", "data"):
                if key in parsed_json and isinstance(parsed_json[key], list):
                    raw_list = parsed_json[key]
                    break
            if not raw_list:
                raw_list = [parsed_json]
    else:
        # Texto plano
        raw_list = [{"content": text.strip()}]

    records: list[dict[str, str]] = []

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

    return records
