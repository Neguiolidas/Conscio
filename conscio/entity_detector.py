"""EntityDetector — simplified, Conscio-only (no i18n).

Ported concept from MemPalace entity_detector.py, drastically simplified:
- Regex patterns with re.UNICODE (supports Portuguese accents: São, João, Ção)
- Detects: persons/projects (capitalized words), domains (x.y.tld), versions (vX.Y.Z)
- Stopword filter (common capitalized English words: The, That, This, etc)
- KG integration: store detected entities in KnowledgeGraph (no inferred relations)
"""
from __future__ import annotations
import re
from typing import Optional

from .kg import KnowledgeGraph


# Word-boundary regex with Unicode letter support (handles á, ç, ã, etc)
_CAPITAL_PATTERN = re.compile(r"\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿA-ZÀ-ÖØ-Þ]+\b", re.UNICODE)
_DOMAIN_PATTERN = re.compile(r"\b[a-z0-9][-a-z0-9.]*\.[a-z]{2,}(?:\.[a-z]{2,})?\b", re.IGNORECASE)
_VERSION_PATTERN = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-z0-9]+)?\b", re.IGNORECASE)

_STOPWORDS = {
    "The", "This", "That", "These", "Those", "There", "Then", "Where",
    "When", "What", "Who", "Why", "How", "Are", "Was", "Were", "Have",
    "Has", "Had", "Will", "Would", "Could", "Should", "Does", "Did",
    "From", "Into", "Over", "Under", "Above", "Below", "Between",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "I", "Me", "My", "We", "He", "She", "It", "They",
}


class EntityDetector:
    """Detect entities (persons, projects, domains, versions) in text."""

    def __init__(self, kg: Optional[KnowledgeGraph] = None):
        self.kg = kg

    def detect(self, text: str) -> list[dict]:
        """Return list of detected entities with type and name."""
        if not text:
            return []
        found: list[dict] = []
        seen: set[str] = set()

        # Persons/projects (capitalized words, 3+ chars, not stopwords)
        for m in _CAPITAL_PATTERN.finditer(text):
            name = m.group(0)
            if name in _STOPWORDS or len(name) < 3:
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            found.append({"name": name, "type": "entity", "evidence": "capitalized"})

        # Domains (URL-like)
        for m in _DOMAIN_PATTERN.finditer(text):
            name = m.group(0).lower()
            # Filter false positives — domain must contain a dot and TLD-ish suffix
            if "." not in name:
                continue
            # Skip filenames (.txt, .md) but allow .com, .com.br, .org etc
            suffix = name.rsplit(".", 1)[-1]
            if suffix in {"txt", "md", "py", "js", "ts", "html", "json", "yaml", "yml", "pdf"}:
                continue
            if name in seen:
                continue
            seen.add(name)
            found.append({"name": name, "type": "domain", "evidence": "url-pattern"})

        # Versions (v3.1.0 or 3.1.0)
        for m in _VERSION_PATTERN.finditer(text):
            name = m.group(0)
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            found.append({"name": name, "type": "version", "evidence": "version-pattern"})

        return found

    def detect_and_store(self, text: str) -> list[dict]:
        """Detect entities and store them in the KnowledgeGraph (if attached).

        Does NOT infer relations — those will be added later via LLM or
        explicit caller. Returns the list of detected entities.
        """
        found = self.detect(text)
        if self.kg is not None:
            for ent in found:
                self.kg.add_entity(ent["name"], entity_type=ent["type"])
        return found
