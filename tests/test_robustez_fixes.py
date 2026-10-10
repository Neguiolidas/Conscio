"""Verificação dos fixes aplicados aos módulos robustez + pipeline.

Roda com: python -m pytest tests/ -q   (ou python tests/test_robustez_fixes.py)
Stdlib apenas.
"""

import contextlib
import math
import os
import tempfile
import time
import unittest

from conscio.robustez import (
    approval,
    decision,
    deltaops,
    llmretry,
    logitjev,
    normalize,
    outcomes_ext,
    pipeline,
    retry,
    singleflight,
    ssrf,
    stateledger,
    toolwrap,
    untrusted,
    verifier,
)


class TestDecisionFix(unittest.TestCase):
    def test_entropy_nats_uses_natural_log(self):
        r = decision.SystemOneRequest(state="aab")
        # H("aab") = -(2/3 ln 2/3 + 1/3 ln 1/3) nats
        expected = -((2 / 3) * math.log(2 / 3) + (1 / 3) * math.log(1 / 3))
        self.assertAlmostEqual(r.entropy_nats(), expected, places=9)


class TestSsrfFixes(unittest.TestCase):
    def test_ipv4_mapped_public_unwrapped(self):
        # ::ffff:8.8.8.8 é IPv4 público mapeado: deve passar agora
        self.assertTrue(ssrf.is_ip_public(__import__("ipaddress").ip_address("::ffff:8.8.8.8")))

    def test_ipv4_mapped_private_still_blocked(self):
        self.assertFalse(ssrf.is_ip_public(__import__("ipaddress").ip_address("::ffff:10.0.0.1")))

    def test_dns_timeout_exists(self):
        import inspect
        src = inspect.getsource(ssrf.assert_safe_url)
        self.assertIn("_resolve_with_timeout", src)

    def test_resolve_timeout_raises(self):
        import socket as _socket
        orig = _socket.getaddrinfo

        def slow(*a, **k):
            time.sleep(5)
            return orig(*a, **k)

        _socket.getaddrinfo = slow
        try:
            with self.assertRaises(ValueError) as ctx:
                ssrf._resolve_with_timeout("example.com", 443, timeout_s=0.05)
            self.assertIn("timed out", str(ctx.exception))
        finally:
            _socket.getaddrinfo = orig


class TestRetryFix(unittest.TestCase):
    def test_streak_resets_on_success(self):
        s = retry.new_state()
        retry.record_failure(s, "t1")
        retry.record_failure(s, "t1")
        retry.record_success(s)
        self.assertEqual(s["failure_streak"], 0)
        self.assertEqual(s["failures"], 0)
        # dedup restaurada: mesma posição do streak → mesma chave
        k1 = retry.record_failure(retry.new_state(), "t1")["alert_key"]
        s2 = retry.new_state()
        retry.record_failure(s2, "t1")
        retry.record_success(s2)
        k2 = retry.record_failure(s2, "t1")["alert_key"]
        self.assertEqual(k1, k2)


class TestApprovalFixes(unittest.TestCase):
    def test_expired_tokens_pruned(self):
        g = approval.ApprovalGate(ttl_seconds=0.01)
        g.register("c1")
        g.register("c2")
        self.assertEqual(len(g._active), 2)
        time.sleep(0.03)
        g.register("c3")  # prune acontece no register
        self.assertEqual(len(g._active), 1)

    def test_claim_compare_digest(self):
        import inspect
        src = inspect.getsource(approval.ApprovalGate.claim)
        self.assertIn("compare_digest", src)

    def test_claim_still_works(self):
        g = approval.ApprovalGate(ttl_seconds=60)
        tok = g.register("conv")
        self.assertTrue(g.claim("conv", tok))
        self.assertFalse(g.claim("conv", tok))  # single-use


class TestStateLedgerFix(unittest.TestCase):
    def test_memory_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            stateledger.StateLedger(
                ":memory:", states={"new", "running", "done", "failed", "blocked"},
                transitions={"new": {"running"}, "running": {"new", "done", "failed", "blocked"},
                             "blocked": {"new"}},
                initial="new", claimed_state="running",
            )
        self.assertIn(":memory:", str(ctx.exception))


class TestUntrustedFix(unittest.TestCase):
    def test_source_quotes_escaped(self):
        out = untrusted.mark_untrusted("dado", source='x" onmouseover="alert(1)')
        self.assertNotIn('onmouseover="alert(1)"', out)
        self.assertIn("onmouseover", out)  # conteúdo escapado, não atributo

    def test_opening_tag_neutralized(self):
        out = untrusted.mark_untrusted('a <untrusted_data source="evil"> b')
        self.assertNotIn('<untrusted_data source="evil">', out)


class TestNormalizeFix(unittest.TestCase):
    def test_truncation_reported_via_stats(self):
        payload = "[" + ",".join(f'{{"content":"c{i}"}}' for i in range(70)) + "]"
        stats: dict = {}
        recs = normalize.normalize_records(payload, stats=stats)
        self.assertEqual(len(recs), 50)
        self.assertEqual(stats, {"total": 70, "returned": 50, "truncated": True})

    def test_no_truncation_stats(self):
        stats: dict = {}
        recs = normalize.normalize_records('[{"content":"x"}]', stats=stats)
        self.assertEqual(len(recs), 1)
        self.assertEqual(stats["truncated"], False)

    def test_backward_compat_no_stats(self):
        recs = normalize.normalize_records('[{"content":"x"}]')
        self.assertEqual(len(recs), 1)


class TestVerifierFix(unittest.TestCase):
    def test_bare_import_error_is_invalid_gate(self):
        g = verifier.GateResult(name="t", passed=False, error="ImportError: cannot import name X")
        self.assertTrue(verifier.is_gate_invalid(g))


class TestLogitjevFix(unittest.TestCase):
    def test_render_answer_gets_question_not_prompt(self):
        seen: dict = {}

        class FakeJev(logitjev.OpenAICompatJev):
            def _chat(self, payload):
                seen["prompt"] = payload["messages"][1]["content"]
                return {"choices": [{"logprobs": {"content": [
                    {"top_logprobs": [
                        {"token": "A", "logprob": -0.1},
                        {"token": "B", "logprob": -2.5},
                    ]}
                ]}}]}

        jev = FakeJev(base_url="http://x", model="m")
        ans = jev.ask(decision.Choice(options=("foo", "bar")), state="estado de teste aqui")
        # o campo question não pode ser o prompt montado com os labels
        self.assertNotIn("reply with the letter only", ans["question"])
        self.assertEqual(ans["probabilities"][0] > ans["probabilities"][1], True)


class TestLlmRetryFix(unittest.TestCase):
    def _exc(self, code):
        e = Exception(f"boom {code}")
        e.status_code = code
        return e

    def test_403_not_quota(self):
        self.assertFalse(llmretry._default_is_quota(self._exc(403)))

    def test_403_raises_immediately(self):
        def call(item, **kw):
            raise self._exc(403)
        with self.assertRaises(Exception) as ctx:
            llmretry.call_with_fallback(call, ["a", "b"], sleep=lambda s: None)
        self.assertIn("403", str(ctx.exception))

    def test_402_still_quota_fallback(self):
        calls = []

        def call(item, **kw):
            calls.append(item)
            if item == "a":
                raise self._exc(402)
            return "ok-b"

        out = llmretry.call_with_fallback(call, ["a", "b"], sleep=lambda s: None)
        self.assertEqual(out, "ok-b")
        self.assertEqual(calls, ["a", "b"])


class TestDeltaopsFix(unittest.TestCase):
    def test_copy_import_at_top(self):
        import inspect
        src = inspect.getsource(deltaops)
        self.assertIn("import copy", src.split("def apply_ops")[0])


class TestPipeline(unittest.TestCase):
    def test_failure_to_outcome_mapping(self):
        f = toolwrap.ToolFailure
        self.assertEqual(pipeline.failure_to_outcome(f("rate_limit", "x", 429)), outcomes_ext.Outcome.RATE_LIMITED)
        self.assertEqual(pipeline.failure_to_outcome(f("timeout", "x")), outcomes_ext.Outcome.TIMEOUT)
        self.assertEqual(pipeline.failure_to_outcome(f("http_error", "x", 403)), outcomes_ext.Outcome.PERMISSION_DENIED)
        self.assertEqual(pipeline.failure_to_outcome(f("http_error", "x", 400)), outcomes_ext.Outcome.INVALID_REQUEST)
        self.assertEqual(pipeline.failure_to_outcome(f("http_error", "x", 500)), outcomes_ext.Outcome.FAILED)
        self.assertEqual(pipeline.failure_to_outcome(f("network_error", "x")), outcomes_ext.Outcome.UNKNOWN)
        self.assertEqual(pipeline.failure_to_outcome(f("internal_error", "x")), outcomes_ext.Outcome.UNKNOWN)

    def test_call_tool_success_with_flight_and_retry(self):
        flight = singleflight.SingleFlight()
        st = retry.new_state()

        class FakeResp:
            status = 200

            def read(self, n=-1):
                return b'{"ok": true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                return FakeResp()

        orig = toolwrap.call_json
        try:
            def patched(url, payload, **kw):
                return orig(url, payload, opener=FakeOpener(), **kw)
            toolwrap.call_json = patched
            data, outcome, failure = pipeline.call_tool(
                "http://x", {"a": 1}, flight=flight, flight_key="k1",
                retry_state=st, task_id="t1",
            )
        finally:
            toolwrap.call_json = orig
        self.assertEqual(data, {"ok": True})
        self.assertEqual(outcome, outcomes_ext.Outcome.SUCCESS)
        self.assertIsNone(failure)
        self.assertEqual(st["failures"], 0)

    def test_tracked_work_happy_path(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            ledger = stateledger.StateLedger(
                path,
                states={"new", "running", "done", "failed", "blocked"},
                transitions={"new": {"running"},
                             "running": {"new", "done", "failed", "blocked"},
                             "blocked": {"new"}},
                initial="new", claimed_state="running",
            )
            with pipeline.tracked_work(ledger, "item1", "w1", 60) as claimed:
                self.assertEqual(claimed["item_id"], "item1")
            # done: item saiu do estado inicial
            nxt = ledger.claim_next("w2", 60)
            self.assertIsNone(nxt)
        finally:
            os.unlink(path)

    def test_tracked_work_failure_transitions_to_failed(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            ledger = stateledger.StateLedger(
                path,
                states={"new", "running", "done", "failed", "blocked"},
                transitions={"new": {"running"},
                             "running": {"new", "done", "failed", "blocked"},
                             "blocked": {"new"}},
                initial="new", claimed_state="running",
            )
            with self.assertRaises(RuntimeError), \
                    pipeline.tracked_work(ledger, "item2", "w1", 60):
                raise RuntimeError("boom")
            # falhou: item pode ser reivindicado de novo? não — foi pra failed.
            # revive e tenta de novo
            self.assertTrue(ledger.revive_blocked("item2") is False)  # failed != blocked
        finally:
            os.unlink(path)

    def test_tracked_work_approval_gate(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            ledger = stateledger.StateLedger(
                path,
                states={"new", "running", "done", "failed", "blocked"},
                transitions={"new": {"running"},
                             "running": {"new", "done", "failed", "blocked"},
                             "blocked": {"new"}},
                initial="new", claimed_state="running",
            )
            gate = approval.ApprovalGate(ttl_seconds=60)
            tok = gate.register("conv1")
            with pipeline.tracked_work(ledger, "item3", "w1", 60, gate=gate,
                                       conversation="conv1", approval_token=tok):
                pass
            # token errado → PermissionError e item liberado
            gate2 = approval.ApprovalGate(ttl_seconds=60)
            with self.assertRaises(PermissionError), \
                    pipeline.tracked_work(ledger, "item4", "w1", 60, gate=gate2,
                                          conversation="c", approval_token="errado"):
                pass
        finally:
            os.unlink(path)

    def test_refresh_doc_prunes_retracted(self):
        doc = {"sections": [], "based_on": ["world:1", "world:2"]}
        ops = [{"op": "add_section", "name": "S"}]
        out = pipeline.refresh_doc(doc, ops, live_fact_ids={"world:1"})
        self.assertEqual(out["status"], "pruned")
        self.assertEqual(out["retracted_ids"], ["world:2"])
        self.assertEqual(out["document"]["based_on"], ["world:1"])
        self.assertEqual(len(out["applied"]), 1)

    def test_judge_flow_with_fake_jev(self):
        class FakeJev(logitjev.OpenAICompatJev):
            def _chat(self, payload):
                return {"choices": [{"logprobs": {"content": [
                    {"top_logprobs": [{"token": "Yes", "logprob": -0.05}]}
                ]}}]}

        jev = FakeJev(base_url="http://x", model="m")
        gates = [lambda: verifier.GateResult(name="g1", passed=True)]
        out = pipeline.judge(decision.Noul(question_hint="vai?"), "estado", jev, gates=gates)
        self.assertTrue(out["verdict"].passed)
        self.assertAlmostEqual(out["answer"]["noul"], 0.9512, places=3)

    def test_sanitize_history_and_log(self):
        h = [{"role": "assistant", "tool_calls": [{"id": "1", "x": 1}]},
             {"role": "user", "content": "oi"}]
        clean = pipeline.sanitize_history_for_llm(h)
        self.assertEqual(clean, [{"role": "user", "content": "oi"}])
        self.assertIn("redacted", pipeline.safe_log_context("sk-abc12345678901234567890 resto"))


class _RedirectServer:
    """Local HTTP server: /hop redirects to `target`, /ok answers 'fine'."""

    def __init__(self, target: str) -> None:
        import http.server
        import threading

        outer = self
        self.hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                if self.path == "/hop":
                    self.send_response(302)
                    self.send_header("Location", outer.target)
                    self.end_headers()
                    return
                body = b"fine"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.target = target.replace("{base}", self.base)
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestReviewFixes(unittest.TestCase):
    """The four defects the review found in the zip, each beside its legit case."""

    # 1. fetch_external: every redirect hop is SSRF-checked, not only the first URL.

    def test_redirect_to_internal_address_is_refused(self):
        # Loopback (port 80, closed here): refused by ssrf for the address, as
        # cloud metadata is; if the fix regresses, no metadata request goes out.
        from conscio.robustez import ingest
        with _RedirectServer("http://127.0.0.1/latest/meta-data/") as srv, \
                self.assertRaises(ingest.IngestRedirectRefused):
            ingest.fetch_text(srv.base + "/hop", timeout_s=5,
                              validate_redirect=ssrf.assert_safe_url)

    def test_redirect_to_allowed_target_still_works(self):
        from conscio.robustez import ingest
        with _RedirectServer("{base}/ok") as srv:
            text = ingest.fetch_text(srv.base + "/hop", timeout_s=5,
                                     validate_redirect=lambda url: None)
        self.assertEqual(text, "fine")
        self.assertEqual(srv.hits, ["/hop", "/ok"])

    def test_fetch_external_wires_the_ssrf_validator(self):
        from unittest import mock
        with mock.patch.object(pipeline.ssrf, "assert_safe_url") as safe, \
                mock.patch.object(pipeline.ingest, "fetch_text", return_value="[]") as ft:
            pipeline.fetch_external("https://example.com/x")
        self.assertIs(ft.call_args.kwargs["validate_redirect"], safe)

    # 2. tracked_work claims ITS item, never the oldest one in the queue.

    def _ledger(self, d):
        return stateledger.StateLedger(
            os.path.join(d, "l.db"),
            states=["pending", "running", "done", "failed", "blocked"],
            transitions={"pending": {"running"},
                         "running": {"done", "failed", "pending", "blocked"},
                         "blocked": {"pending"}},
            initial="pending", claimed_state="running", blocked_state="blocked")

    def test_tracked_work_leaves_older_items_alone(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._ledger(d)
            ledger.add_item("older")
            with pipeline.tracked_work(ledger, "newer", "w1", 60) as claimed:
                self.assertEqual(claimed["item_id"], "newer")
            self.assertEqual(self._row(d, "older"), ("pending", 0))
            self.assertEqual(self._row(d, "newer")[0], "done")

    @staticmethod
    def _row(d, item_id):
        import sqlite3
        with contextlib.closing(sqlite3.connect(os.path.join(d, "l.db"))) as conn:
            return conn.execute("SELECT state, attempts FROM ledger_items WHERE item_id = ?",
                                (item_id,)).fetchone()

    def test_tracked_work_refuses_an_item_owned_by_another_worker(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._ledger(d)
            ledger.add_item("busy")
            self.assertIsNotNone(ledger.claim("busy", "w-other", 60))
            with self.assertRaises(RuntimeError), \
                    pipeline.tracked_work(ledger, "busy", "w1", 60):
                pass

    # 3. judge: the appeal cap lives in a caller-held Arbiter, so it holds across calls.

    def _jev(self):
        class FakeJev(logitjev.OpenAICompatJev):
            def _chat(self, payload):
                return {"choices": [{"logprobs": {"content": [
                    {"top_logprobs": [{"token": "Yes", "logprob": -0.05}]}]}}]}
        return FakeJev(base_url="http://x", model="m")

    def test_appeal_without_arbiter_is_refused(self):
        with self.assertRaises(ValueError):
            pipeline.judge(decision.Noul(question_hint="q"), "s", self._jev(),
                           arbiter_judge=lambda p: {"ruling": "overturn"}, task_id="t1")

    def test_second_appeal_for_same_task_is_capped(self):
        from conscio.robustez import arbiter
        calls = []

        def arb_judge(prompt):
            calls.append(prompt)
            return {"ruling": "overturn", "reason": "r"}

        arb = arbiter.Arbiter()
        gates = [lambda: verifier.GateResult(name="g", passed=True)]
        kw = {"gates": gates, "judge_fn": lambda a: (False, "subjective no"),
              "arbiter_judge": arb_judge, "task_id": "t1", "arb": arb}
        first = pipeline.judge(decision.Noul(question_hint="q"), "s", self._jev(), **kw)
        second = pipeline.judge(decision.Noul(question_hint="q"), "s", self._jev(), **kw)
        self.assertTrue(first["ruling"].overturned)
        self.assertFalse(second["ruling"].overturned)
        self.assertEqual(len(calls), 1)

    # 4. logitjev counts only the labels the prompt asked for.

    def test_option_text_token_is_not_an_answer(self):
        top = [{"token": "proceed", "logprob": -0.01}, {"token": "B", "logprob": -3.0}]
        probs = logitjev._probs_from_top(top, ["A", "B"])
        self.assertEqual(set(probs), {"B"})

    def test_no_listed_label_means_no_answer(self):
        top = [{"token": "proceed", "logprob": -0.01}, {"token": "hold", "logprob": -0.5}]
        self.assertIsNone(logitjev._probs_from_top(top, ["A", "B"]))

    def test_yes_no_case_insensitive_still_counts(self):
        probs = logitjev._probs_from_top([{"token": " yes", "logprob": 0.0}], ["Yes", "No"])
        self.assertEqual(probs, {"Yes": 1.0})




def _jev_answering(top: list[dict]) -> logitjev.OpenAICompatJev:
    """A Jev whose endpoint returns `top` as the first token's top-k."""
    class FakeJev(logitjev.OpenAICompatJev):
        def _chat(self, payload):
            return {"choices": [{"logprobs": {"content": [{"top_logprobs": top}]}}]}
    return FakeJev(base_url="http://x", model="m")


class TestLogitjevLabels(unittest.TestCase):
    """Every answer label is one character, so max_tokens=1 can always emit it."""

    def test_ninth_option_is_offered_to_the_model(self):
        q = decision.Choice(options=tuple(f"opt{i}" for i in range(9)))
        self.assertIn("I) opt8", logitjev.build_user_text(q, "s"))

    def test_ninth_option_vote_is_counted(self):
        q = decision.Choice(options=tuple(f"opt{i}" for i in range(9)))
        ans = _jev_answering([{"token": "I", "logprob": 0.0}]).ask(q, state="s")
        self.assertEqual(ans["probabilities"][8], 1.0)

    def test_options_beyond_the_label_alphabet_are_refused_before_the_call(self):
        class NoCallJev(logitjev.OpenAICompatJev):
            def _chat(self, payload):
                raise AssertionError("endpoint must not be called")
        q = decision.Choice(options=tuple(f"opt{i}" for i in range(27)))
        with self.assertRaises(ValueError):
            NoCallJev(base_url="http://x", model="m").ask(q, state="s")

    def test_every_score_label_is_a_single_character(self):
        for levels in range(2, decision.MAX_SCORE_LEVELS + 1):
            q = decision.Score(levels=levels)
            self.assertTrue(all(len(lab) == 1 for lab in logitjev._answer_labels(q)), levels)

    def test_top_score_vote_lands_on_the_top_level(self):
        # A digit-splitting tokenizer emits "10" as "1" + "0"; with max_tokens=1
        # the top vote must not be read as level 1.
        q = decision.Score(levels=10)
        ans = _jev_answering([{"token": "9", "logprob": 0.0}]).ask(q, state="s")
        self.assertEqual(ans["probabilities"][-1], 1.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
