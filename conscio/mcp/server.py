# conscio/mcp/server.py
"""Engine-backed bindings (propose-only) + the stdio serve loop + the
conscio-mcp CLI. stdout is the protocol channel; logging goes to stderr."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from conscio import __version__
from conscio.engine import ConsciousnessEngine
from conscio.workspace import WorkspaceContext

from . import jsonrpc as j
from .protocol import SUPPORTED_PROTOCOLS, Dispatcher
from .schemas import (ACT_TOOL_DEFS, BASE_TOOL_DEFS, LIAISON_TOOL_DEFS,
                      RELAY_TOOL_DEFS, RESOURCE_DEFS, derive_event_id,
                      event_to_frame, validate_event)
from .seen import SeenStore
from ..agency import review_apply
from ..liaison import mailbox, relay, review

# v2.6.3 #2: floor between auto-review SQL polls so --auto-review does not open a
# liaison SELECT on every single tool call in a chatty session. host_act.approve
# remains the authority; this only paces the opportunistic poll.
AUTO_APPLY_THROTTLE_S = 5.0


class Bindings:
    def __init__(self, engine: ConsciousnessEngine, seen: SeenStore, *,
                 adapter_name: str | None = None,
                 workspace_id: str = "", act_flag: bool = False,
                 hermes_review: bool = False,
                 reviewers: tuple[str, ...] = (),
                 self_instance_id: str = "",
                 liaison_db: Path | None = None,
                 relay: bool = False,
                 relay_peers: tuple[str, ...] = (),
                 auto_review: bool = False) -> None:
        self.engine = engine
        self.seen = seen
        self.adapter_name = adapter_name
        self.workspace_id = workspace_id
        self.act_flag = act_flag             # v2.0.1: --enable-act
        self._act_error = ""
        self.hermes_review = hermes_review    # v2.6.0: --enable-hermes-review
        self.reviewers = tuple(reviewers)
        self.self_instance_id = self_instance_id
        self.liaison_db: Path = (Path(liaison_db) if liaison_db is not None
                                 else mailbox.default_db())
        self.relay = relay                   # v2.6.1: --enable-relay
        self.relay_peers = tuple(relay_peers)
        self.auto_review = auto_review        # v2.6.2: --auto-review
        self.last_auto_apply_ts = 0.0         # v2.6.3 #2: throttle clock

    # ── discovery ──
    def version(self) -> str:
        return __version__

    def on_initialize(self, params: dict) -> None:
        """v2.0.1: read the host tool manifest from initialize params and enable
        act. Independent of session init — a bad/missing manifest never
        half-enables act (act stays off, act_error set)."""
        self._act_error = ""
        if not self.act_flag:
            return
        tools = (params.get("conscio") or {}).get("tools")
        if not tools:
            self._act_error = "no tool manifest in initialize params"
            return
        if not self.engine.enable_host_act(tools):
            self._act_error = self.engine.host_act_error or "act not enabled"

    def _act_enabled(self) -> bool:
        return self.act_flag and self.engine.host_act is not None

    def conscio_meta(self) -> dict:
        ha = self.engine.host_act
        return {"workspace_id": self.workspace_id,
                "awake": bool(self.engine.awake),
                "act_enabled": self._act_enabled(),
                "act_error": self._act_error,
                "host_tools_count": len(ha.registry.names()) if ha else 0,
                "adapter_ready": self.adapter_name is not None,
                "manifest_hash": getattr(self.engine, "_host_act_hash", ""),
                "adapter": self.adapter_name,
                "hermes_review_enabled": self.hermes_review,
                "reviewers_count": len(self.reviewers),
                "relay_enabled": self.relay,
                "relay_peers_count": len(self.relay_peers),
                "auto_review_enabled": self.auto_review,
                "supported_protocols": SUPPORTED_PROTOCOLS}

    def tool_defs(self) -> list[dict]:
        defs = list(BASE_TOOL_DEFS)
        if self._act_enabled():
            defs += list(ACT_TOOL_DEFS)
        if self.hermes_review:
            for d in LIAISON_TOOL_DEFS:
                if d["name"] == "conscio.poll_reviews" and not self._act_enabled():
                    continue                 # proposer tool needs act too
                defs.append(d)
        if self.relay:
            defs += list(RELAY_TOOL_DEFS)
        return defs

    def resource_defs(self) -> list[dict]:
        return list(RESOURCE_DEFS)

    # ── tool dispatch ──
    def call_tool(self, name: str, args: dict) -> dict:
        self._maybe_auto_apply()             # v2.6.2: opportunistic on dispatch
        fn = self._tools().get(name)
        if fn is None:
            raise j.MethodNotFound(f"tool '{name}' not available")
        return {"content": [{"type": "text", "text": json.dumps(fn(args))}]}

    def _tools(self):
        tools = {
            "conscio.feed": self._feed,
            "conscio.note": self._note,
            "conscio.advisory": lambda a: self.engine.advisory(),
            "conscio.recall": lambda a: {"snippets": self.engine.recall(
                self._require(a, "query"), int(a.get("k", 3)),
                a.get("categories"))},
            "conscio.propose_action": lambda a: self.engine.propose_action(
                self._require(a, "intent")),
            "conscio.propose_plan": lambda a: self.engine.propose_plan(
                self._require(a, "goal"), a.get("tools")),
            "conscio.state": lambda a: self._state_payload(),
            "conscio.events": lambda a: self._events_payload(a),
            "conscio.handoff": lambda a: self._handoff_payload(),
            "conscio.structure": self._structure,
            "conscio.structural_lookup": self._structural_lookup,
            "conscio.cognitive_cycle": self._cognitive_cycle,
            "conscio.evaluate": lambda a: self.engine.evaluate(
                a.get("task_description", ""), a.get("output")).to_dict(),
            # ── v3.0 Gate tools ──
            "conscio.decide": lambda a: self.engine.decide(
                title=a.get("title", ""), context=a.get("context", ""),
                alternatives=a.get("alternatives"), adr_id=a.get("adr_id"),
                status=a.get("status", "proposed"), deciders=a.get("deciders")),
            "conscio.council": lambda a: self.engine.council(
                question=a.get("question", ""), context=a.get("context", ""),
                options=a.get("options")),
            "conscio.loop_gate": lambda a: self.engine.loop_gate(
                task=a.get("task", ""), frequency=a.get("frequency", ""),
                verifiable=a.get("verifiable", True),
                budget_ok=a.get("budget_ok", True),
                has_tools=a.get("has_tools", True)),
            "conscio.delivery_check": lambda a: self.engine.delivery_check(),
            "conscio.investigate": lambda a: self.engine.investigate(
                target=a.get("target", ""), action_type=a.get("action_type", "")),
            # ── v3.0 Pipeline tools ──
            "conscio.acceptance_criteria": lambda a: self.engine.acceptance_criteria(
                goal=a.get("goal", ""), depth=a.get("depth", ""),
                risk_domains=a.get("risk_domains")),
            "conscio.verify": lambda a: self.engine.verify(
                criteria=a.get("criteria"), criteria_source=a.get("criteria_source", "")),
            "conscio.continuous_loop": lambda a: self.engine.continuous_loop(
                task=a.get("task", ""), pattern=a.get("pattern", ""),
                frequency=a.get("frequency", ""),
                verifiable=a.get("verifiable", True),
                budget_ok=a.get("budget_ok", True),
                has_tools=a.get("has_tools", True)),
            "conscio.strategic_compact": lambda a: self.engine.strategic_compact(
                phase=a.get("phase", ""), context_tokens=a.get("context_tokens", 0),
                context_window=a.get("context_window", 0)),
            "conscio.ledger": lambda a: self.engine.ledger(
                action=a.get("action", "record"), rollout_id=a.get("rollout_id"),
                candidates=a.get("candidates"), fresh_info=a.get("fresh_info", ""),
                search_space_size=a.get("search_space_size", 0),
                marks=a.get("marks"), prior_winner=a.get("prior_winner", ""),
                coherence_mark=a.get("coherence_mark")),
            # ── v3.0 Diagnostic tools ──
            "conscio.context_budget": lambda a: self.engine.context_budget(
                context_tokens=a.get("context_tokens", 0),
                context_window=a.get("context_window", 0),
                detail=a.get("detail", "summary")),
            "conscio.eval_harness": lambda a: self.engine.eval_harness(
                action=a.get("action", "define"),
                eval_id=a.get("eval_id"),
                eval_type=a.get("eval_type", "capability"),
                task=a.get("task", ""),
                criteria=a.get("criteria"),
                results=a.get("results"),
                k_values=a.get("k_values")),
            "conscio.rules_distill": lambda a: self.engine.rules_distill(
                action=a.get("action", "scan"),
                source_types=a.get("source_types"),
                min_occurrences=a.get("min_occurrences", 2),
                rule_text=a.get("rule_text", ""),
                rule_id=a.get("rule_id")),
        }
        if self._act_enabled():
            ha = self.engine.host_act
            assert ha is not None  # guarded by _act_enabled()
            tools.update({
                "conscio.act": self._act,
                "conscio.report_result": self._report_result,
                "conscio.pending": lambda a, _ha=ha: _ha.pending(
                    self._int_arg(a, "limit", 20)),
                "conscio.approve": lambda a, _ha=ha: _ha.approve(
                    self._int_arg(a, "ledger_id")),
                "conscio.reject": lambda a, _ha=ha: _ha.reject(
                    self._int_arg(a, "ledger_id"), str(a.get("reason", ""))),
            })
        if self.hermes_review:
            tools["conscio.reviews"] = self._reviews
            tools["conscio.review_approve"] = self._review_approve
            tools["conscio.review_reject"] = self._review_reject
            if self._act_enabled():
                tools["conscio.poll_reviews"] = self._poll_reviews
        if self.relay:
            tools["conscio.relay_send"] = self._relay_send
            tools["conscio.relay_inbox"] = self._relay_inbox
            tools["conscio.relay_read"] = self._relay_read
            tools["conscio.relay_broadcast"] = self._relay_broadcast
        return tools

    def _int_arg(self, args: dict, key: str,
                 default: int | None = None) -> int:
        if key not in args:
            if default is not None:
                return default
            raise j.InvalidParams(f"missing '{key}'")
        val = args[key]
        if isinstance(val, bool) or not isinstance(val, int):  # not str/float/None
            raise j.InvalidParams(f"'{key}' must be an integer")
        return val

    def _report_result(self, args: dict) -> dict:
        ledger_id = self._int_arg(args, "ledger_id")
        result = self._require(args, "result")
        if not isinstance(result, dict):
            raise j.InvalidParams("result must be an object")
        ha = self.engine.host_act
        assert ha is not None  # only callable via act tools
        return ha.report(ledger_id, result)

    # ── v2.6.0 Liaison: reviewer side (pure mailbox; no host_act) ──
    def _reviews(self, args: dict) -> list[dict]:
        limit = self._int_arg(args, "limit", 50)
        rows = mailbox.inbox(self.liaison_db, self.self_instance_id,
                             types=["review_request"], unread_only=True,
                             limit=limit)
        out: list[dict] = []
        seen_fp: set[str] = set()
        for m in rows:                       # newest-first -> first per fp = latest
            try:
                rq = review.parse_request(m["payload"])
            except ValueError:
                continue
            if rq.fp in seen_fp:
                continue
            seen_fp.add(rq.fp)
            out.append({"fp": rq.fp, "from_instance": m["from_instance"],
                        "tool": rq.tool, "args": rq.args, "goal": rq.goal,
                        "verdict": rq.verdict, "ts": m["ts"]})
        return out

    def _review_approve(self, args: dict) -> dict:
        return self._send_verdict(args, "approve")

    def _review_reject(self, args: dict) -> dict:
        return self._send_verdict(args, "reject")

    def _send_verdict(self, args: dict, decision: str) -> dict:
        fp = self._require(args, "fp")
        reason = str(args.get("reason", ""))
        rows = mailbox.inbox(self.liaison_db, self.self_instance_id,
                             types=["review_request"], unread_only=True,
                             limit=200)
        match = [m for m in rows if (m["payload"] or {}).get("fp") == fp]
        if not match:
            return {"ok": False, "reason": "unknown_fp"}
        proposer = match[0]["from_instance"]
        mailbox.send(self.liaison_db, from_instance=self.self_instance_id,
                     to_instance=proposer, type="review_verdict",
                     payload=review.build_verdict(fp=fp, decision=decision,
                                                  reason=reason))
        mailbox.mark_read(self.liaison_db, [m["id"] for m in match])  # all fp rows
        return {"ok": True, "fp": fp, "decision": decision, "to": proposer}

    # ── v2.6.0 Liaison: proposer side ──
    def _maybe_publish_review(self, result: dict) -> None:
        if not (self.hermes_review and self.reviewers):
            return
        if result.get("status") != "pending_approval":
            return
        if result.get("approval_policy") != "hermes_review":
            return
        row = self.engine.host_act.ledger.get(result["ledger_id"]) if self.engine.host_act else None  # Hermet R1
        if row is None:
            return
        args = self._row_args(row)
        fp = review.fingerprint(self.self_instance_id, row["goal_fp"],
                                row["tool"], args, row["id"])
        payload = review.build_request(
            fp=fp, tool=row["tool"], args=args,
            goal=row.get("goal_text", ""), verdict=row.get("verdict", ""),
            rationale=row.get("rationale", ""))
        try:                                       # best-effort: the act is already
            for r in self.reviewers:               # ledgered pending; publish is 2nd
                mailbox.send(self.liaison_db, from_instance=self.self_instance_id,
                             to_instance=r, type="review_request", payload=payload)
        except Exception as exc:                   # never break act on a bad mailbox
            print(f"liaison: review_request publish failed: {exc}",
                  file=sys.stderr)

    @staticmethod
    def _row_args(row: dict) -> dict:
        try:
            args = json.loads(row.get("args_json") or "{}")
        except (TypeError, ValueError):
            return {}
        return args if isinstance(args, dict) else {}

    def _poll_reviews(self, args: dict) -> list[dict]:
        limit = self._int_arg(args, "limit", 50)
        return review_apply.apply_verdicts(
            self.engine.host_act, self.liaison_db, self.self_instance_id,
            self.reviewers, limit=limit)

    def _maybe_auto_apply(self) -> None:
        """v2.6.2: when armed (--auto-review) and the engine is awake, apply
        inbound allowlisted verdicts to pending acts. Inert without act/reviewers;
        never raises into a request (a bad mailbox must not break a tool call)."""
        if not (self.auto_review and getattr(self.engine, "awake", False)):
            return
        if self.engine.host_act is None or not self.reviewers:
            return
        now = time.monotonic()               # v2.6.3 #2: pace the SQL poll
        if now - self.last_auto_apply_ts < AUTO_APPLY_THROTTLE_S:
            return
        self.last_auto_apply_ts = now
        try:
            review_apply.apply_verdicts(self.engine.host_act, self.liaison_db,
                                        self.self_instance_id, self.reviewers)
        except Exception as exc:             # never break a request
            print(f"auto-review apply failed: {exc}", file=sys.stderr)

    # ── v2.6.1 Relay: general cross-agent messaging (pure mailbox; no engine) ──
    def _relay_send(self, args: dict) -> dict:
        to = str(args.get("to", ""))
        mtype = str(args.get("type", ""))
        payload = args.get("payload", {})
        try:
            relay.validate_send(to=to, type=mtype, payload=payload,
                                peers=set(self.relay_peers))
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        mid = mailbox.send(self.liaison_db, from_instance=self.self_instance_id,
                           to_instance=to, type=mtype, payload=payload)
        try:                                      # R2 best-effort retention
            mailbox.purge_read(self.liaison_db, relay.RETENTION_DAYS)
        except Exception as exc:
            print(f"liaison: relay purge failed: {exc}", file=sys.stderr)
        return {"ok": True, "id": mid}

    def _relay_broadcast(self, args: dict) -> dict:
        """v2.8.2: fan-out a relay message to ALL allowlisted peers. Best-effort
        per peer (a failing peer never aborts the rest); a mailbox write, never
        host_act -> daemon-perceives/server-acts holds."""
        mtype = str(args.get("type", ""))
        payload = args.get("payload", {})
        peers = set(self.relay_peers)
        sent: list[dict] = []
        errors: list[dict] = []
        for peer in self.relay_peers:
            try:
                relay.validate_send(to=peer, type=mtype, payload=payload,
                                    peers=peers)
            except ValueError as exc:
                errors.append({"to": peer, "reason": str(exc)})
                continue
            try:
                mid = mailbox.send(self.liaison_db,
                                   from_instance=self.self_instance_id,
                                   to_instance=peer, type=mtype,
                                   payload=payload)
            except Exception as exc:      # per-peer isolation: keep fanning out
                errors.append({"to": peer,
                               "reason": f"send failed: {type(exc).__name__}"})
                continue
            sent.append({"to": peer, "id": mid})
        if sent:                                  # best-effort retention, once
            try:
                mailbox.purge_read(self.liaison_db, relay.RETENTION_DAYS)
            except Exception as exc:
                print(f"liaison: relay purge failed: {exc}", file=sys.stderr)
        return {"ok": True, "sent": sent, "errors": errors}

    def _relay_inbox(self, args: dict) -> dict:
        limit = self._int_arg(args, "limit", 50)
        rows = mailbox.inbox(self.liaison_db, self.self_instance_id,
                             types=None, unread_only=True, limit=limit)
        peers = set(self.relay_peers)
        out: list[dict] = []
        junk: list[int] = []
        for r in rows:
            if r.get("type") in relay.RESERVED_TYPES:
                continue                          # review channel owns it
            if relay.is_relay_message(r, peers):
                out.append({k: r[k] for k in
                            ("id", "from_instance", "type", "payload", "ts")})
            else:
                junk.append(r["id"])              # non-peer / oversized
        if junk:
            mailbox.mark_read(self.liaison_db, junk)
        return {"messages": out}

    def _relay_read(self, args: dict) -> dict:
        ids = [i for i in args.get("ids", [])
               if isinstance(i, int) and not isinstance(i, bool)]  # R1-menor
        n = mailbox.mark_read(self.liaison_db, ids)
        return {"ok": True, "marked": n}

    @staticmethod
    def _require(args: dict, key: str):
        if key not in args:
            raise j.InvalidParams(f"missing '{key}'")
        return args[key]

    def _structure(self, args: dict) -> dict:
        """Report the currently loaded workspace structure (consent-gated).

        Read-only: the graph is synced at server startup (see
        _sync_structure_at_startup). Returns loaded=False when none is loaded.
        """
        sig = self.engine.structural_signal()
        if sig is None:
            return {"loaded": False}
        from conscio.structural import render_structural, structural_budget
        budget = structural_budget(self.engine.model_info.context_window)
        return {
            "loaded": True,
            "node_count": sig.node_count,
            "link_count": sig.link_count,
            "hyperedges": len(sig.hyperedges),
            "communities": len(sig.communities),
            "built_at_commit": sig.built_at_commit,
            "content_hash": sig.content_hash,
            "digest": render_structural(sig, budget),
        }

    def _structural_lookup(self, args: dict) -> dict:
        """Resolve a structural node / hyperedge / community id to detail."""
        key = self._require(args, "key")
        return {"result": self.engine.structural_lookup(str(key))}

    def _cognitive_cycle(self, args: dict) -> dict:
        """Run one explicit cognitive pass and return a per-stage report.

        The act stage runs only when act is enabled on this server; otherwise
        it is propose-only (reflect/synthesize/self-improve). Accepts
        session_tokens so the loop also feeds the metabolic tier.
        """
        st = args.get("session_tokens")
        if isinstance(st, int) and not isinstance(st, bool) and st >= 0:
            self.engine.session_tokens_used = st
        return self.engine.cognitive_cycle(
            world_state=args.get("world_state", "") or "",
            act=self._act_enabled())

    def _feed(self, args: dict) -> dict:
        event = self._require(args, "event")
        errors = validate_event(event)
        if errors:
            raise j.InvalidParams("; ".join(errors))
        eid = derive_event_id(event)
        prior = self.seen.seen(eid)
        if prior is not None:
            return json.loads(prior)
        # Host-reported live context usage feeds the metabolic tier. Reject
        # bad values (bools are ints; negatives) so a malformed field can't
        # poison the tier. Absent -> engine keeps its prior/None (healthy).
        st = args.get("session_tokens")
        if isinstance(st, int) and not isinstance(st, bool) and st >= 0:
            self.engine.session_tokens_used = st
        world_state = event_to_frame(event).to_world_state()
        self.engine.perceive(world_state)
        self.engine.reflect(world_state)
        result = {"event_id": eid, "advisory": self.engine.advisory()}
        self.seen.mark(eid, json.dumps(result),
                       float(event.get("ts") or time.time()))
        return result

    def _note(self, args: dict) -> dict:
        event = self._require(args, "event")
        errors = validate_event(event)
        if errors:
            raise j.InvalidParams("; ".join(errors))
        eid = derive_event_id(event)
        prior = self.seen.seen(eid)
        if prior is not None:
            return json.loads(prior)
        self.engine.event_bus.emit(
            type="host:event", category="external",
            data={"host_type": event["type"],
                  "host_category": event["category"],
                  "source": event["source"],
                  "payload": event.get("payload", {}) or {}})
        result = {"event_id": eid, "noted": True}
        self.seen.mark(eid, json.dumps(result),
                       float(event.get("ts") or time.time()))
        return result

    def _act(self, args: dict) -> dict:
        intent = self._require(args, "intent")
        if not isinstance(intent, dict):
            raise j.InvalidParams("intent must be an object")
        key = intent.get("idempotency_key")
        skey = None
        if key is not None:
            if not isinstance(key, str) or len(key) > 256:
                raise j.InvalidParams("idempotency_key must be a str <= 256 chars")
            skey = f"act:{self.workspace_id}:{intent.get('tool', '')}:{key}"
            prior = self.seen.seen(skey)
            if prior is not None:
                return json.loads(prior)
        result = self.engine.host_act.propose(intent) if self.engine.host_act else {"status": "error", "message": "act not enabled"}
        if skey is not None:
            self.seen.mark(skey, json.dumps(result), time.time())
        self._maybe_publish_review(result)
        return result

    # ── read-only state payloads (shared by resources + tools, v2.4) ──
    def _state_payload(self) -> dict:
        return self.engine.advisory().get("state", {})

    def _events_payload(self, params: dict) -> list[dict]:
        def s(key: str) -> str | None:
            v = params.get(key)
            return str(v) if v not in (None, "") else None
        try:
            limit = int(params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        rows = self.engine.event_bus.query(
            type=s("type"), category=s("category"), since=s("since"), limit=limit)
        return [e.to_dict() for e in rows]

    def _handoff_payload(self) -> str:
        return self._handoff_text()

    # ── resources ──
    def read_resource(self, uri: str) -> dict:
        parsed = urlparse(uri)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if base == "conscio://advisory":
            return self._json_resource(uri, self.engine.advisory())
        if base == "conscio://state":
            return self._json_resource(uri, self._state_payload())
        if base == "conscio://events":
            q = parse_qs(parsed.query)
            params = {k: (v[0] if v else None) for k, v in q.items()}
            return self._json_resource(uri, self._events_payload(params))
        if base == "conscio://handoff":
            return {"contents": [{"uri": uri, "mimeType": "text/markdown",
                                 "text": self._handoff_payload()}]}
        raise j.InvalidParams(f"unknown resource '{uri}'")

    @staticmethod
    def _json_resource(uri: str, data: Any) -> dict:
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                             "text": json.dumps(data)}]}

    def _handoff_text(self) -> str:
        path = Path(self.engine.storage) / "handoff" / "_session_handoff.md"
        try:
            return path.read_text()
        except OSError:
            return ""


def serve(bindings: Bindings, instream, outstream, *,
          max_bytes: int = j.DEFAULT_MAX_FRAME_BYTES) -> None:
    dispatcher = Dispatcher(bindings)
    for frame in j.read_frames(instream, max_bytes):
        if frame is j.OVERSIZE:
            _write(outstream, j.make_error(None, j.INVALID_REQUEST,
                                           "frame too large"))
            continue
        try:
            msg = json.loads(frame)
        except json.JSONDecodeError:
            _write(outstream, j.make_error(None, j.PARSE_ERROR, "parse error"))
            continue
        if not isinstance(msg, dict):
            _write(outstream, j.make_error(None, j.INVALID_REQUEST,
                                           "request must be an object"))
            continue
        response = dispatcher.handle(msg)
        if response is not None:
            _write(outstream, response)


def _write(outstream, obj: dict) -> None:
    outstream.write(json.dumps(obj) + "\n")
    outstream.flush()


def _build_adapter(spec: str):
    """spec forms: 'mock' | 'ollama:<model>'."""
    from conscio.agency import MockAdapter, OllamaAdapter
    if spec == "mock":
        return MockAdapter(script=[])
    if spec.startswith("ollama:"):
        return OllamaAdapter(model=spec.split(":", 1)[1])
    raise SystemExit(f"unsupported --adapter '{spec}'")


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="conscio-mcp",
                                     description="Conscio MCP stdio server")
    parser.add_argument("--storage", default=None)
    parser.add_argument("--model", default=None,
                        help="model name, 'auto' for auto-detect, or omit")
    parser.add_argument("--base-url", default=None,
                        help="OpenAI-compatible endpoint to probe")
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--max-frame-bytes", type=int,
                        default=j.DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--seen-max-rows", type=int, default=10_000)
    parser.add_argument("--seen-max-age-days", type=int, default=30)
    parser.add_argument("--enable-act", action="store_true",
                        help="enable opt-in host-executed audited act")
    parser.add_argument("--awake", action="store_true",
                        help="set the engine Awake at startup (act gate)")
    parser.add_argument("--enable-hermes-review", action="store_true",
                        help="enable opt-in cross-agent hermes_review comms")
    parser.add_argument("--reviewer", action="append", default=[],
                        metavar="INSTANCE_ID",
                        help="trusted reviewer instance_id (repeatable)")
    parser.add_argument("--liaison-db", default=None,
                        help="mailbox db path (default $HERMES_HOME/liaison.db)")
    parser.add_argument("--enable-relay", action="store_true",
                        help="enable opt-in general cross-agent messaging")
    parser.add_argument("--relay-peer", action="append", default=[],
                        metavar="INSTANCE_ID",
                        help="trusted relay peer instance_id (repeatable)")
    parser.add_argument("--auto-review", action="store_true",
                        help="auto-apply inbound review verdicts each request "
                             "when awake (needs --enable-act + "
                             "--enable-hermes-review)")
    return parser


def _resolve_model(args) -> str:
    """Resolve the model name: --model > config.json 'model' > CONSCIO_MODEL.

    v3.1: --model auto triggers auto-detection from the LM Studio /v1/models
    endpoint. Tests each model with a minimal prompt and returns the first
    that responds. Persists the winner to ~/.config/conscio/config.json so
    the next boot skips detection.

    Mirrors the daemon's precedence so a host that registers ``conscio-mcp``
    with only ``--storage`` (e.g. the Claude Code bundle) still picks up the
    ``model`` from ~/.config/conscio/config.json instead of failing to start.
    Raises ``ValueError`` when none of the three channels supplies a model.
    """
    from conscio.adapter_config import load_config
    from conscio.models import resolve_model_name

    # v3.1: auto-detect mode
    if args.model == "auto":
        detected, all_models = _auto_detect_model(args.base_url)
        if detected is not None:
            _persist_model(detected)
            # Store all_models for FallbackAdapter construction in main()
            args._auto_fallback_models = all_models
            return detected
        # fall through to normal resolution if auto-detect failed

    return resolve_model_name(cli_arg=args.model if args.model != "auto" else None,
                              config_model=load_config().get("model"))


def _auto_detect_model(base_url: str | None) -> tuple[str | None, list[str]]:
    """v3.1: Auto-detect working models from the LM Studio /v1/models endpoint.

    Returns (first_working_model, all_chat_models).
    The first_working_model is used for immediate startup; all_chat_models
    is used to build a FallbackAdapter chain.
    """
    import json as _json
    import urllib.request

    url = (base_url or "http://localhost:1234/v1").rstrip("/")
    models_url = f"{url}/models"

    try:
        req = urllib.request.Request(models_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        print(f"[conscio-mcp] auto-detect: cannot reach {models_url}: {exc}",
              file=sys.stderr)
        return None, []

    models = data.get("data", [])
    if not models:
        print("[conscio-mcp] auto-detect: no models available", file=sys.stderr)
        return None, []

    # Filter out embedding models (they can't do chat completions)
    chat_models = [
        m["id"] for m in models
        if "embed" not in m["id"].lower()
        and "nomic" not in m["id"].lower()
    ]

    if not chat_models:
        print("[conscio-mcp] auto-detect: no chat models found (all embedding?)",
              file=sys.stderr)
        return None, []

    print(f"[conscio-mcp] auto-detect: {len(chat_models)} chat models found: "
          f"{', '.join(chat_models[:5])}{'...' if len(chat_models) > 5 else ''}",
          file=sys.stderr)

    # Test each model with a minimal prompt to find the first that works
    for model_id in chat_models:
        if _test_model(url, model_id):
            print(f"[conscio-mcp] auto-detect: '{model_id}' responded OK",
                  file=sys.stderr)
            return model_id, chat_models
        print(f"[conscio-mcp] auto-detect: '{model_id}' failed, trying next...",
              file=sys.stderr)

    print("[conscio-mcp] auto-detect: no model responded to test prompt",
          file=sys.stderr)
    return None, chat_models


def _test_model(base_url: str, model_id: str) -> bool:
    """Send a minimal chat completion to test if a model is working."""
    import json as _json
    import urllib.request

    payload = _json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "Respond with: ok"}],
        "max_tokens": 5,
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return len(text.strip()) > 0
    except Exception:
        return False


def _persist_model(model_id: str) -> None:
    """v3.1: Persist the detected model to ~/.config/conscio/config.json.

    So the next boot skips auto-detection and starts instantly.
    """
    import os
    from pathlib import Path

    config_dir = Path(os.path.expanduser("~/.config/conscio"))
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    existing = {}
    if config_path.exists():
        try:
            import json as _json
            with open(config_path) as f:
                existing = _json.load(f)
        except Exception:
            existing = {}

    existing["model"] = model_id

    try:
        import json as _json
        with open(config_path, "w") as f:
            _json.dump(existing, f, indent=2)
        print(f"[conscio-mcp] auto-detect: persisted '{model_id}' to {config_path}",
              file=sys.stderr)
    except Exception as exc:
        print(f"[conscio-mcp] auto-detect: failed to persist config: {exc}",
              file=sys.stderr)


def _sync_structure_at_startup(engine, workspace) -> str:
    """Bring the engine's structure in line with consent for ``workspace``.

    The daemon does this each cycle; the MCP server (Claude Code) serves one
    workspace per process, so it syncs once at startup. Consent-gated and
    non-fatal — a missing/OFF consent or a bad graph never blocks startup.
    Returns sync_structure's status verb, or ``skip:<err>``.
    """
    from conscio.structural_consent import (
        StructuralConsent, consent_path, sync_structure)
    try:
        consent = StructuralConsent(consent_path(engine.storage))
        return sync_structure(engine, workspace, consent)
    except Exception as exc:                       # never block startup
        return f"skip:{exc}"


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    from conscio.installer.binding import validate_binding   # R6
    validate_binding(args.storage)
    try:
        model_name = _resolve_model(args)
    except ValueError:
        print("Error: no model specified. Set CONSCIO_MODEL, configure 'model' "
              "in config.json, or pass --model.", file=sys.stderr)
        return 1
    engine = ConsciousnessEngine(model_name, storage_path=args.storage,
                                  base_url=args.base_url)
    from conscio.adapter_config import build_adapter_from_config, load_config
    adapter_name = None
    if args.adapter:                               # CLI wins (mock | ollama:..)
        engine.attach_adapter(_build_adapter(args.adapter))
        adapter_name = args.adapter
    else:
        adapter, atype = build_adapter_from_config(load_config(),
                                                   fallback_model=model_name)
        if adapter is not None:
            engine.attach_adapter(adapter)
            adapter_name = atype
    if args.awake:
        engine.wake()                              # reuse the existing R9 toggle
    # v3.1: if auto-detect found multiple models, use FallbackAdapter
    fallback_models = getattr(args, "_auto_fallback_models", None)
    if fallback_models and len(fallback_models) > 1 and not args.adapter:
        from .agency.fallback_adapter import FallbackAdapter
        base_url = args.base_url or "http://localhost:1234/v1"
        fb_adapter = FallbackAdapter(
            base_url=base_url, models=fallback_models,
        )
        engine.attach_adapter(fb_adapter)
        adapter_name = "fallback"
        print(f"[conscio-mcp] fallback chain: {' -> '.join(fallback_models[:5])}"
              f"{'...' if len(fallback_models) > 5 else ''}",
              file=sys.stderr)
    elif args.adapter:                               # CLI wins (mock | ollama:..)
        engine.attach_adapter(_build_adapter(args.adapter))
        adapter_name = args.adapter
    else:
        adapter, atype = build_adapter_from_config(load_config(),
                                                   fallback_model=model_name)
        if adapter is not None:
            engine.attach_adapter(adapter)
            adapter_name = atype
    workspace = WorkspaceContext().current()
    structure_status = _sync_structure_at_startup(engine, workspace)
    seen = SeenStore(Path(engine.storage) / "mcp_seen.db")
    seen.prune(args.seen_max_rows, args.seen_max_age_days)
    self_instance_id = ""
    liaison_db = None
    if args.enable_hermes_review or args.enable_relay:
        from conscio.noosphere.identity import load_or_create
        self_instance_id = load_or_create(engine.storage).instance_id
        liaison_db = (Path(args.liaison_db) if args.liaison_db
                      else mailbox.default_db())
    bindings = Bindings(engine, seen, adapter_name=adapter_name,
                        workspace_id=workspace.id, act_flag=args.enable_act,
                        hermes_review=args.enable_hermes_review,
                        reviewers=tuple(args.reviewer),
                        self_instance_id=self_instance_id,
                        liaison_db=liaison_db,
                        relay=args.enable_relay,
                        relay_peers=tuple(args.relay_peer),
                        auto_review=args.auto_review)
    mode = "act" if args.enable_act else "propose-only"
    if args.enable_hermes_review:
        if args.reviewer:
            mode += f"+hermes-review(reviewers={len(args.reviewer)})"
        else:                              # active but no recipients (Hermet)
            mode += "+hermes-review(reviewers=0; no publish targets)"
    if args.enable_relay:
        if args.relay_peer:
            mode += f"+relay(peers={len(args.relay_peer)})"
        else:                              # active but no send/recv targets
            mode += "+relay(peers=0; no send/recv targets)"
    if args.auto_review:
        mode += "+auto-review"
    print(f"conscio-mcp {__version__} ready "
          f"(workspace={workspace.id}, mode={mode}, "
          f"structure={structure_status})", file=sys.stderr)
    try:
        serve(bindings, sys.stdin, sys.stdout, max_bytes=args.max_frame_bytes)
    finally:
        seen.close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
