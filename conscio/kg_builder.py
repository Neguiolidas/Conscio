"""kg_builder — Entity extraction from ContentStore into KnowledgeGraph (v3.3.1).

Scans ContentStore chunks, extracts entities via regex heuristics (no external NLP deps),
and populates the KnowledgeGraph with entities + triples. Supports incremental runs via
a checkpoint stored in the KG entity properties.

Heuristic extraction patterns (stdlib only):
- URLs, domains, IPs, email addresses
- File paths and function/method names (code identifiers)
- Capitalized multi-word terms (proper nouns)
- Known technical entities (model names, frameworks, tools)

Triples are generated from co-occurrence: entities found in the same chunk
get a ``co_occurs_with`` relationship.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .content_store import ContentStore
from .kg import KnowledgeGraph

log = logging.getLogger("conscio.kg_builder")

# ── Entity extraction patterns ─────────────────────────────────────────

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+")
_DOMAIN_PATTERN = re.compile(r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?){1,5}\b")
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_FILEPATH_PATTERN = re.compile(r"(?:/[a-zA-Z0-9_.-]+)+(?:\.[a-zA-Z0-9]+)?")
_CAPITALIZED_TERM = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
_CODE_IDENTIFIER = re.compile(r"\b[a-z_][a-z0-9_]{2,}(?:\.[a-z_][a-z0-9_]{2,})*\(?")
_VERSION_PATTERN = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9]+)?\b")

# Known technical entities we want to catch regardless of case
_TECH_ENTITIES = {
    "conscio", "hermes", "orion", "prompts", "claude", "anthropic",
    "openai", "nvidia", "docker", "kubernetes", "postgres", "redis",
    "sqlite", "python", "javascript", "typescript", "react", "node",
    "tailscale", "cloudflare", "firebase", "supabase", "pytorch",
    "tensorflow", "llama", "mistral", "deepseek", "minimax",
    "nemotron", "kimi", "glm", "modal", "anydesk", "xrdp",
}


@dataclass
class ExtractedEntity:
    name: str
    entity_type: str
    properties: dict | None = None


@dataclass
class ExtractionResult:
    entities: list[ExtractedEntity]
    chunk_id: int
    source_id: int


def extract_entities(text: str) -> list[ExtractedEntity]:
    """Extract entities from text using regex heuristics. No external deps."""
    found: dict[str, str] = {}  # name -> type (dedup)
    text_lower = text.lower()

    # URLs
    for m in _URL_PATTERN.finditer(text):
        name = m.group().rstrip(".,;:)!]")
        found[name] = "url"

    # Emails
    for m in _EMAIL_PATTERN.finditer(text):
        found[m.group()] = "email"

    # IPs
    for m in _IP_PATTERN.finditer(text):
        found[m.group()] = "ip_address"

    # File paths (absolute paths) — skip if part of a URL
    for m in _FILEPATH_PATTERN.finditer(text):
        path = m.group()
        if path.startswith("/") and len(path) > 5:
            # Skip if it's part of a URL (preceded by ://)
            start = m.start()
            if start >= 3 and text[start-3:start] == "://":
                continue
            if not any(scheme in text[max(0,start-10):start] for scheme in ["http://", "https://", "ftp://"]):
                found[path] = "filepath"

    # Domains (avoid catching common words)
    for m in _DOMAIN_PATTERN.finditer(text):
        domain = m.group().lower()
        if domain.count(".") >= 2 and len(domain) > 8:
            found[domain] = "domain"

    # Versions
    for m in _VERSION_PATTERN.finditer(text):
        found[m.group()] = "version"

    # Technical entities (lowercase match) — BEFORE capitalized terms
    # so that known tech names get the correct type
    for tech_name in _TECH_ENTITIES:
        if tech_name in text_lower:
            idx = text_lower.find(tech_name)
            actual = text[idx:idx + len(tech_name)]
            if (idx == 0 or not text[idx-1].isalnum()) and \
               (idx + len(tech_name) >= len(text) or not text[idx + len(tech_name)].isalnum()):
                found[actual] = "technology"

    # Capitalized multi-word terms (proper nouns) — skip if already typed
    for m in _CAPITALIZED_TERM.finditer(text):
        term = m.group()
        if len(term) < 5:
            continue
        if term.lower() in ("the", "this", "that", "these", "those", "what", "when", "where"):
            continue
        if term not in found:  # don't override technology type
            found[term] = "concept"

    # Code identifiers (snake_case function/variable names)
    for m in _CODE_IDENTIFIER.finditer(text):
        ident = m.group()
        if ident.endswith("("):
            ident = ident[:-1]
        if len(ident) >= 4 and "_" in ident:
            found[ident] = "identifier"

    return [ExtractedEntity(name=name, entity_type=etype) for name, etype in found.items()]


def build_triples(
    entities: list[ExtractedEntity], source_id: int
) -> list[tuple[str, str, str, float]]:
    """Generate triples from co-occurring entities in the same chunk.

    Returns list of (subject_name, predicate, object_name, confidence).
    """
    triples: list[tuple[str, str, str, float]] = []
    # Every entity relates to the source
    for e in entities:
        triples.append((e.name, "mentioned_in", f"source:{source_id}", 1.0))

    # Co-occurrence pairs (entities found together)
    if len(entities) >= 2:
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                a, b = entities[i], entities[j]
                triples.append((a.name, "co_occurs_with", b.name, 0.7))
                triples.append((b.name, "co_occurs_with", a.name, 0.7))

    return triples


class KGBuilder:
    """Incremental entity extractor: ContentStore → KnowledgeGraph.

    Usage::
        builder = KGBuilder(cs, kg)
        builder.run()  # process all unprocessed sources
        builder.run(limit=100)  # process at most 100 sources
    """

    def __init__(self, content_store: ContentStore, kg: KnowledgeGraph):
        self.cs = content_store
        self.kg = kg
        self._checkpoint_key = "kg_builder_last_source"

    def _get_checkpoint(self) -> int:
        """Get the last processed source_id from KG metadata entity."""
        meta = self.kg.query_entity(f"_{self._checkpoint_key}")
        if meta:
            props = json.loads(meta.get("properties", "{}"))
            return int(props.get("last_source_id", 0))
        return 0

    def _set_checkpoint(self, source_id: int) -> None:
        """Persist checkpoint to KG."""
        self.kg.add_entity(
            f"_{self._checkpoint_key}",
            entity_type="metadata",
            properties={"last_source_id": source_id, "updated_at": time.time()},
        )

    def run(self, limit: int = 500) -> dict:
        """Incremental extraction run.

        Args:
            limit: Max sources to process in this run

        Returns:
            dict with entities_added, triples_added, sources_scanned
        """
        checkpoint = self._get_checkpoint()
        log.info("KG builder starting from source_id > %d (limit=%d)", checkpoint, limit)

        # Query new sources via ContentStore SQLite directly (no FTS5 needed)
        conn = self.cs.db
        rows = conn.execute(
            "SELECT id, label, source_category, content_hash FROM sources "
            "WHERE id > ? ORDER BY id LIMIT ?",
            (checkpoint, limit),
        ).fetchall()
        if not rows:
            log.info("No new sources to process")
            return {"entities_added": 0, "triples_added": 0, "sources_scanned": 0}

        entities_added = 0
        triples_added = 0
        max_id_seen = checkpoint

        for row in rows:
            source_id = int(row["id"])
            label = row["label"]
            category = row["source_category"]
            max_id_seen = max(max_id_seen, source_id)

            # Get chunks for this source
            chunks = conn.execute(
                "SELECT rowid, title, content FROM chunks WHERE source_id = ?",
                (source_id,),
            ).fetchall()

            for chunk_row in chunks:
                chunk_id = chunk_row["rowid"]
                content = chunk_row["content"] or ""

                # Skip empty or very short content
                if len(content.strip()) < 30:
                    continue

                # Extract entities
                entities = extract_entities(content)
                if not entities:
                    continue

                # Add entities to KG
                for ent in entities:
                    try:
                        self.kg.add_entity(
                            ent.name,
                            entity_type=ent.entity_type,
                            properties={
                                "source_id": source_id,
                                "source_label": label,
                                "source_category": category,
                            },
                        )
                        entities_added += 1
                    except Exception:
                        log.debug("Failed to add entity '%s':", ent.name, exc_info=True)

                # Build and add triples
                triples = build_triples(entities, source_id)
                for subj, pred, obj, conf in triples:
                    try:
                        self.kg.add_triple(
                            subj, pred, obj,
                            confidence=conf,
                            source=f"content_store:{source_id}",
                        )
                        triples_added += 1
                    except Exception:
                        log.debug("Failed to add triple '%s':", f"{subj}-{pred}-{obj}", exc_info=True)

        # Update checkpoint
        if max_id_seen > checkpoint:
            self._set_checkpoint(max_id_seen)

        log.info(
            "KG builder done: %d entities, %d triples from %d sources",
            entities_added, triples_added, len(rows),
        )
        return {
            "entities_added": entities_added,
            "triples_added": triples_added,
            "sources_scanned": len(rows),
        }