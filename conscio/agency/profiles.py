# conscio/agency/profiles.py
"""
ModelProfile + ProbeSuite — the core of "any model" (spec section 5.10).

Five micro-probes (~2k tokens total) measure what the attached model can
actually do; the result decides the gateway tier, the skeptic mode and
how many tools the actor sees. Lazy: probes run on engine.probe() or on
the first run() cycle — never at attach, never inside reflect() (P6).
Valid results are cached in SQLite by model_name; a profile where every
probe errored (backend down) is marked invalid and never cached — the
pipeline then keeps its caps-based defaults. No hardcoded model table.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .adapter import AdapterError, InferenceAdapter
from .gateway import parse_kv, repair_json

VISIBLE_TOOLS_SMALL = 5      # catalog cap for weak profiles (spec 5.5)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_profiles (
    model_name TEXT PRIMARY KEY,
    has_json_mode INTEGER NOT NULL,
    supports_gbnf INTEGER NOT NULL,
    json_fidelity REAL NOT NULL,
    schema_depth INTEGER NOT NULL,
    kv_ok INTEGER NOT NULL,
    instruction_depth INTEGER NOT NULL,
    probed_at REAL NOT NULL
);
"""


@dataclass
class ModelProfile:
    model_name: str
    has_json_mode: bool = False
    supports_gbnf: bool = False
    json_fidelity: float = 0.0
    schema_depth: int = 0
    kv_ok: bool = False
    instruction_depth: int = 0
    valid: bool = False          # False = every probe errored (no signal)
    probed_at: float = 0.0


def choose_tier(profile: ModelProfile) -> str | None:
    """supports_gbnf -> T1; json_mode and fidelity >= 0.8 -> T2; else T3.

    None for invalid profiles: the gateway keeps caps-based auto.
    """
    if not profile.valid:
        return None
    if profile.supports_gbnf:
        return "T1"
    if profile.has_json_mode and profile.json_fidelity >= 0.8:
        return "T2"
    return "T3"


def skeptic_mode(profile: ModelProfile) -> str:
    """Open critique needs reliable nested JSON; checklist otherwise."""
    if profile.json_fidelity >= 0.8 and profile.schema_depth >= 2:
        return "open"
    return "checklist"


def max_visible_tools(profile: ModelProfile) -> int | None:
    """None = full catalog; small models get the safest 5 (spec 5.5)."""
    if profile.schema_depth >= 2 and profile.instruction_depth >= 2:
        return None
    return VISIBLE_TOOLS_SMALL


def prompt_complexity(profile: ModelProfile) -> str:
    """v3.1: adaptive prompt complexity based on model profile.

    Returns 'full', 'compact', or 'minimal':
    - full:    persona + tool catalog + state + memories + few-shot (capable models)
    - compact: persona (short) + tool catalog + state (no memories, no few-shot)
    - minimal: tool catalog + state only (tiny models, persona hurts)

    The persona prompt adds ~200 tokens of instructions. For models <2B,
    those tokens compete with the tool schema for attention, degrading
    JSON quality. Stripping the persona lets the model focus on what matters:
    the tools and the goal.
    """
    if not profile.valid:
        return "full"  # unknown model — give full prompt, let retry handle it
    if profile.instruction_depth >= 3 and profile.schema_depth >= 3:
        return "full"
    if profile.instruction_depth >= 2 and profile.schema_depth >= 2:
        return "compact"
    return "minimal"


# ── the five probes (name, prompt, scorer) ─────────────────────────────

def _json_or_none(raw: str):
    try:
        return json.loads(repair_json(raw))
    except (json.JSONDecodeError, ValueError):
        return None


def _p1(raw: str) -> bool:
    return _json_or_none(raw) == {"status": "ok", "count": 3}


def _p2(raw: str) -> bool:
    data = _json_or_none(raw)
    if not isinstance(data, dict):
        return False
    plan = data.get("plan")
    return (isinstance(plan, dict) and isinstance(plan.get("tool"), str)
            and isinstance(plan.get("steps"), list))


def _p3(raw: str) -> bool:
    data = _json_or_none(raw)
    return (isinstance(data, dict)
            and data.get("color") in ("red", "green", "blue"))


def _p4(raw: str) -> bool:
    return _json_or_none(raw) == {"name": "probe"}


def _p5(raw: str) -> bool:
    data = parse_kv(raw)
    return data.get("tool") == "fs_read" and bool(data.get("rationale"))


PROBES = (
    ("p1_flat_json",
     'Return ONLY this exact JSON object, nothing else: '
     '{"status": "ok", "count": 3}', _p1),
    ("p2_nested",
     'Return ONLY a JSON object with one key "plan" whose value is an '
     'object with a string key "tool" and a list-of-strings key "steps". '
     'No other keys, no prose.', _p2),
    ("p3_enum",
     'Return ONLY a JSON object {"color": X} where X is exactly one of: '
     '"red", "green", "blue". No prose.', _p3),
    ("p4_negative",
     'Return ONLY a JSON object with a single key "name" whose value is '
     '"probe". Do NOT include any other field. No prose.', _p4),
    ("p5_kv_line",
     "Respond with EXACTLY these two lines and nothing else:\n"
     "TOOL: fs_read\nWHY: probe", _p5),
)


class ProbeSuite:
    """Runs the probes against an adapter; caches by model_name."""

    def __init__(self, adapter: InferenceAdapter, db_path: Path | str):
        self.adapter = adapter
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def get(self, *, force: bool = False) -> ModelProfile:
        name = self.adapter.capabilities().model_name
        if not force:
            row = self._conn.execute(
                "SELECT has_json_mode, supports_gbnf, json_fidelity,"
                " schema_depth, kv_ok, instruction_depth, probed_at"
                " FROM model_profiles WHERE model_name=?", (name,)).fetchone()
            if row:
                return ModelProfile(
                    model_name=name, has_json_mode=bool(row[0]),
                    supports_gbnf=bool(row[1]), json_fidelity=float(row[2]),
                    schema_depth=int(row[3]), kv_ok=bool(row[4]),
                    instruction_depth=int(row[5]), valid=True,
                    probed_at=float(row[6]))
        profile = self.run()
        if profile.valid:                    # no-signal results never cached
            self._conn.execute(
                "INSERT INTO model_profiles (model_name, has_json_mode,"
                " supports_gbnf, json_fidelity, schema_depth, kv_ok,"
                " instruction_depth, probed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(model_name) DO UPDATE SET"
                " has_json_mode=excluded.has_json_mode,"
                " supports_gbnf=excluded.supports_gbnf,"
                " json_fidelity=excluded.json_fidelity,"
                " schema_depth=excluded.schema_depth,"
                " kv_ok=excluded.kv_ok,"
                " instruction_depth=excluded.instruction_depth,"
                " probed_at=excluded.probed_at",
                (profile.model_name, int(profile.has_json_mode),
                 int(profile.supports_gbnf), profile.json_fidelity,
                 profile.schema_depth, int(profile.kv_ok),
                 profile.instruction_depth, profile.probed_at))
            self._conn.commit()
        return profile

    def run(self) -> ModelProfile:
        caps = self.adapter.capabilities()
        results: dict[str, bool] = {}
        answered = False
        for name, prompt, scorer in PROBES:
            try:
                raw = self.adapter.generate(prompt, max_tokens=120,
                                            temperature=0.0).text
            except AdapterError:
                results[name] = False
                continue
            answered = True
            results[name] = bool(scorer(raw))
        json_passes = sum((results["p1_flat_json"], results["p2_nested"],
                           results["p3_enum"], results["p4_negative"]))
        return ModelProfile(
            model_name=caps.model_name,
            has_json_mode=caps.json_mode,
            supports_gbnf=caps.grammar,
            json_fidelity=json_passes / 4.0,
            schema_depth=(2 if results["p2_nested"]
                          else 1 if results["p1_flat_json"] else 0),
            kv_ok=results["p5_kv_line"],
            instruction_depth=(int(results["p3_enum"])
                               + int(results["p4_negative"])),
            valid=answered,
            probed_at=time.time())

    def close(self) -> None:
        self._conn.close()
