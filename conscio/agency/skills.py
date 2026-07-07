# conscio/agency/skills.py
"""
SkillLibrary — procedural memory (spec v1.1 sections 3-5).

Audited plans that succeeded become skills: plan TEMPLATES stored as
data, never code, so safety rule R1 stays untouched. The dream's
Distill sub-phase reads the ActionLedger past a watermark and upserts
one skill per (goal_fp, tool_seq); the Actor receives the best matches
as few-shot exemplars rendered for the active decode tier; act()
settles each cycle's outcome back into the skills that were served.

Lives in the shared conscio.db (same WAL database as ContentStore,
EventBus and the ActionLedger) — no new DB convention.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

MAX_PLAN_STEPS = 5       # longer plans are not useful exemplars for 4B
MIN_SERVE_RATE = 0.5     # never teach a plan that fails half the time
SIMILARITY_FLOOR = 0.2   # Jaccard threshold for lexical goal match
MAX_EXEMPLARS = 2        # prompt budget for small models

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL,
    goal_fp TEXT NOT NULL,
    goal_text TEXT NOT NULL DEFAULT '',
    tool_seq TEXT NOT NULL,
    plan_template TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 1,
    failures INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    last_used_ts REAL NOT NULL DEFAULT 0,
    UNIQUE(goal_fp, tool_seq)
);
CREATE INDEX IF NOT EXISTS idx_skills_goal ON skills(goal_fp);
CREATE TABLE IF NOT EXISTS skills_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _tokens(text: str) -> set[str]:
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {word for word in cleaned.split() if word}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _rate(row: sqlite3.Row) -> float:
    total = row["successes"] + row["failures"]
    return row["successes"] / total if total else 0.0


class SkillLibrary:
    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        # single-slot attribution: skills served for the cycle in flight
        self._served: list[int] | None = None

    # ── Distill (dream sub-phase, spec section 4) ──────────────────────

    def distill(self, ledger: Any, *, dry_run: bool = False) -> int:
        """Turn successful ledger executions past the watermark into
        skills. Returns the number of skills created or reinforced."""
        watermark = int(self._meta_get("distill_watermark", "0"))
        rows = ledger.executed_since(watermark)
        if not rows:
            return 0
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(row["goal_fp"], []).append(row)
        distilled = 0
        for goal_fp, actions in groups.items():
            actions = actions[-MAX_PLAN_STEPS:]
            tool_seq = json.dumps([a["tool"] for a in actions])
            template = json.dumps([
                {"tool": a["tool"], "args": json.loads(a["args_json"]),
                 "rationale": a["rationale"]} for a in actions])
            goal_text = next((a["goal_text"] for a in reversed(actions)
                              if a["goal_text"]), "")
            distilled += 1
            if dry_run:
                continue
            cur = self._conn.execute(
                "UPDATE skills SET successes = successes + 1, goal_text ="
                " CASE WHEN goal_text='' THEN ? ELSE goal_text END"
                " WHERE goal_fp=? AND tool_seq=?",
                (goal_text, goal_fp, tool_seq))
            if cur.rowcount == 0:
                self._conn.execute(
                    "INSERT INTO skills (created_ts, goal_fp, goal_text,"
                    " tool_seq, plan_template) VALUES (?, ?, ?, ?, ?)",
                    (time.time(), goal_fp, goal_text, tool_seq, template))
        if not dry_run:
            self._meta_set("distill_watermark",
                           str(max(row["id"] for row in rows)))
            self._conn.commit()
        return distilled

    # ── Few-shot serving (spec section 5) ──────────────────────────────

    def few_shot(self, goal_text: str, *, tier: str = "T2",
                 k: int = MAX_EXEMPLARS) -> list[str]:
        """Best skills for this goal, rendered for the decode tier.
        Serving records a single-slot attribution consumed by settle()."""
        from . import goal_fingerprint
        goal_fp = goal_fingerprint(goal_text)
        candidates: list[tuple[float, float, int, float, sqlite3.Row]] = []
        for row in self._conn.execute("SELECT * FROM skills").fetchall():
            rate = _rate(row)
            if rate < MIN_SERVE_RATE:
                continue                       # never teach failure (A12)
            if row["goal_fp"] == goal_fp:
                similarity = 1.0
            else:
                similarity = _similarity(goal_text, row["goal_text"])
                if similarity < SIMILARITY_FLOOR:
                    continue
            candidates.append((similarity, rate, row["uses"],
                               row["last_used_ts"], row))
        candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
        chosen = [c[4] for c in candidates[:k]]
        if not chosen:
            self._served = None
            return []
        now = time.time()
        self._conn.executemany(
            "UPDATE skills SET uses = uses + 1, last_used_ts = ?"
            " WHERE id = ?", [(now, int(row["id"])) for row in chosen])
        self._conn.commit()
        self._served = [int(row["id"]) for row in chosen]
        return [self._render(row, tier) for row in chosen]

    def settle(self, report: Any) -> None:
        """Feed the cycle outcome back into the skills served for it.
        EXECUTED rewards, FAILED penalizes (a plan that can't even decode
        or run is evidence against the skill); PROPOSED/REJECTED/LOCKED
        discard the slot without scoring — a human gate never counts
        against the agent (house rule, F2)."""
        served, self._served = self._served, None
        if not served:
            return
        status = getattr(report, "status", None)
        name = getattr(status, "value", str(status))
        if name == "executed":
            column = "successes"
        elif name == "failed":
            column = "failures"
        else:
            return
        self._conn.executemany(
            f"UPDATE skills SET {column} = {column} + 1 WHERE id = ?",
            [(skill_id,) for skill_id in served])
        self._conn.commit()

    # ── promotion (v2.3) ────────────────────────────────────────────────

    def graft(self, goal_fp: str, goal_text: str, tool_seq: str,
              plan_template: str, *, successes: int,
              failures: int) -> int | None:
        """Insert a promoted foreign skill as data, seeded with the trial
        counters it earned locally. The single write seam for foreign skills.
        Never overwrites a local skill sharing (goal_fp, tool_seq): a collision
        returns None."""
        cur = self._conn.execute(
            "INSERT INTO skills (created_ts, goal_fp, goal_text, tool_seq,"
            " plan_template, successes, failures) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(goal_fp, tool_seq) DO NOTHING",
            (time.time(), goal_fp, goal_text, tool_seq, plan_template,
             successes, failures))
        self._conn.commit()
        if cur.rowcount == 0 or cur.lastrowid is None:
            return None
        return int(cur.lastrowid)

    # ── rendering ──────────────────────────────────────────────────────

    @staticmethod
    def _render(row: sqlite3.Row, tier: str) -> str:
        """One exemplar. KV lines for T3, one JSON step per line for
        T1/T2 (a subset without expected_outcome: the gateway appends the
        authoritative format block AFTER the few-shot section, so the
        exemplar teaches tool/arg choice, not response syntax)."""
        steps = json.loads(row["plan_template"])
        total = row["successes"] + row["failures"]
        rate = int(round(100 * row["successes"] / total)) if total else 100
        lines = [f"Past successful plan for a similar goal "
                 f"(success rate {rate}%, used {row['uses']}x):"]
        for step in steps:
            if tier == "T3":
                lines.append(f"TOOL: {step['tool']}")
                for name, value in step["args"].items():
                    lines.append(f"ARG {name} = {value}")
                lines.append(f"WHY: {step['rationale']}")
            else:
                lines.append(json.dumps(
                    {"tool": step["tool"], "args": step["args"],
                     "rationale": step["rationale"]}))
        return "\n".join(lines)

    # ── housekeeping ───────────────────────────────────────────────────

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()
        return int(row[0])

    def all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM skills ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def _meta_get(self, key: str, default: str) -> str:
        row = self._conn.execute(
            "SELECT value FROM skills_meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def _meta_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO skills_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))

    def close(self) -> None:
        self._conn.close()
