# Safety rules (non-negotiable)

These rules only ever grow stronger, never weaker. They are enforced in code, not
just documented.

1. **No autonomous self-modification** — evolution proposals require human
   approval.
2. **Context injection has hard limits** — never exceeds the mode budget.
3. **Goals never execute directly** — only through the audited `act()` pipeline:
   validated output contract + semantic audit (Skeptic) + risk gating + earned
   autonomy (TrustMatrix) + circuit breaker with per-goal quarantine and lockdown.
4. **Reflections are append-only** — never edited once written.
5. **Cannot modify its own safety rules** — no self-referential gate bypass.
6. **HIGH-risk actions always require human approval** — never auto-executed.
7. **No network in the tool registry** — the only network the core may touch is
   the InferenceAdapter (localhost by default); shell lives in the sibling
   `conscio-shell`, outside this repo.
8. **Every external effect goes through the ActionLedger** — append-only,
   auditable.

## How the gates compose

When `act()` proposes an action, it must clear, in order:

1. **Contract validation** — the proposal must match the schema (typed,
   non-empty, enum-checked) or it is rejected at decode.
2. **Skeptic audit** — a hostile auditor (optionally a *different* model) tries to
   refute the action; a FAIL verdict blocks it.
3. **Risk gating** — `LOW`/`MEDIUM`/`HIGH`. HIGH always queues for a human (R6).
4. **Earned autonomy** — `TrustMatrix` grants L1 (propose) / L2 (auto-LOW) / L3
   (heartbeat) only from measured calibration and real ledger history.
5. **Circuit breaker** — repeated failures quarantine a goal; a quorum of
   quarantines latches a global lockdown that survives restart.
6. **Ledger** — every external effect is recorded append-only before and after
   execution.

`reflect()` sits entirely outside this — it has no side effects to gate.
