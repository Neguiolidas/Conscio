# Quality Patterns — a study of the Fable 5 planning corpus

**What this is.** A study, by a later model (Opus 4.8), of the engineering
patterns in the work that built Conscio's agency layer: the F2/F3/F4
specs + plans (`docs/superpowers/{specs,plans}/2026-06-12-*`) and this session's
v2.0 brainstorm. Goal: not to praise, but to extract *transferable* patterns —
how the author reasons about orchestration, adaptive thinking, meta-cognition,
reliability, honesty, and intelligence — precisely enough to apply them.

Each pattern: **the move** — *evidence (where)* — how to apply.

---

## 1. Orchestration

- **The orchestration lives in the shape of the decision space, not the answers.**
  *This session: an open "design v2.0" was decomposed into 6 single-fork
  questions (ambition, sequence, versioning, hardware, umwelt, noosphere), each
  2–4 mutually-exclusive options, each with a marked recommendation + reason, one
  decision per message.* — Before solving, carve the problem into orthogonal
  choices and pre-compute a defensible default for each. The human picks; the
  structure is yours.

- **One plan = one phase = independently-shippable software.** *F2, F3, F4 are
  separate self-contained plans, each ending green + merged + versioned; the
  roadmap is a chain, never a monolith.* — Never write one plan spanning
  subsystems that can ship apart.

- **Lock the file-edit map before writing tasks.** *Every plan opens with an
  Architecture line naming exactly which files change and how: "Surgical edits to
  gateway.py (T1 + explicit tier), adapter.py (Meter/MeteredAdapter), trust.py
  (L3)…".* — Decomposition is decided once, at the top, in concrete paths.

- **TDD micro-step is the atom of execution.** *failing test → run (expect FAIL
  with the exact reason) → minimal impl → run (PASS) → commit; 2–5 min each.* —
  The plan is executable by someone with zero context and questionable taste,
  because every step states its own command and expected output.

---

## 2. Adaptive thinking

- **Serve the spec's motivation over its letter when they conflict — and say so.**
  *F3 deviation #1: the spec said "probe on first `act()`"; doing that would
  consume MockAdapter scripts in every existing test → break zero-regression. So
  probe on `run()`/`probe()` instead, "honoring the spec's actual motivation."* —
  A spec is intent compressed into words. When words fight intent, follow intent
  and document the deviation as a deviation.

- **Separate value-order from build-order.** *This session: value priority
  (Product > Society > Science > Organism) ≠ build sequence
  (Prove → Ship → Live → Connect), because de-risking is not importance.* — Hold
  two orderings at once without collapsing them.

- **Define the absent/broken-input case as first-class.** *F2 #3: a breaker with
  no `db_path` degrades to F1 global-lockdown behavior. F3 #2: a no-signal probe
  (backend down) is `valid=False`, never cached — "no-signal ≠ measurement."* —
  Decide what happens when the dependency is missing, before it goes missing.

- **Adapt the artifact to the consumer's capability.** *F4: one skill, rendered
  as KV-lines for a T3 model, JSON steps for T1/T2.* — Same data, tier-shaped
  surface.

---

## 3. Meta-cognition

- **Tag your own decisions as decisions.** *`[D]` markers throughout the F4 spec;
  "Key design decisions (deviations from spec, justified)" blocks in F2/F3.* —
  Separate "what the spec told me" from "what I chose under autonomy" so a human
  audits exactly the latter. Make your discretion visible.

- **The artifact reviews itself against its source.** *Every plan ends with a
  Self-Review: a spec-coverage map (each requirement → the task that implements
  it), a placeholder scan, a type/name-consistency check.* — Close the loop
  before handoff; never assume coverage, prove it.

- **Name uncertainty as a bounded check, not a hole.** *F2 self-review #4:
  "risks flagged inside the tasks — the Goal id attribute name (Task 7), the
  engine class name (Task 9)… 30-second verifications at the start of each task,
  not placeholders."* — When unsure of a real signature, mark it as a quick
  verification with a location, never paper over it with vagueness.

---

## 4. Reliability

- **Debt-zero down to the linter.** *F4 Task 1: "remove the `datetime` import if
  orphaned (ruff F401 will confirm)" — the dead import is anticipated and the tool
  that catches it is named.* — A change isn't done until the tooling that would
  flag its residue is green.

- **Fail-closed / fail-safe is decided up front, everywhere.** *F2 #10:
  "fail-closed in everything: unparseable audit output = FAIL; adapter error
  during audit = FAIL." Skeptic default = checklist (fail-safe for small
  models).* — Every ambiguous failure has a pre-chosen safe direction.

- **Re-state load-bearing invariants in every artifact.** *"`reflect()` untouched
  (P6). Zero-deps (stdlib + sqlite3) preserved." appears in every plan's
  Architecture block.* — Invariants erode by drift; repetition is the guard.

- **Encode the environment's failure modes as hard rules.** *"Run ONE test file
  at a time (RAM). NEVER `git add -A/-u/.` — explicit paths only" (so internal
  specs never get committed).* — Your own tooling is a hazard; constrain it in the
  plan.

- **Prove with an attack, not a happy path.** *F2 Task 10 / A3: 20 sabotaged
  proposals; acceptance = "100% of deterministic sabotage blocked, ZERO
  execution."* — The acceptance test for a safety boundary is an adversary.

---

## 5. Honesty

- **Verify the premise before executing the task — and report the truth even when
  it contradicts the brief.** *The standout: F4 spec debt #1. The stated debt
  ("`utcnow()` breaks 3.12") was investigated and found **false** — the real CI
  failure was a stale YAML test + ruff; `utcnow()` was only a DeprecationWarning.
  The fix was still done (hygiene) but documented for the honest reason.* — A
  task's stated cause can be wrong. Check it. Say what's true.

- **No theater.** *F4 spec debt #6: `mypy || true` is a gate that can never fail —
  a lie. "No theater": make it a real gate or delete it.* — Refuse decorative
  quality signals.

- **State the limit of every number.** *F3 bench note: "MockAdapter numbers are
  the deterministic harness baseline (validity/catch-rate 1.0 by construction —
  they prove the instrument; the real numbers come from real backends)."* —
  1.0-by-construction is not 1.0-by-performance, and the document says so. (This
  honesty is exactly what the v1.2 "Prove" phase and `CLAIMS.md` exist to close.)

- **Honest accounting of who failed.** *F2 #2: a Skeptic FAIL feeds the breaker;
  a human `reject()` is recorded separately and never counts against the agent.* —
  Attribute failure to the right actor.

---

## 6. Intelligence

- **Express new power as data, so the safety invariant holds for free.** *F4:
  procedural memory = skills as plan **templates** (data), never code → R1 (no
  autonomous self-modification) is untouched.* — The deepest recurring move: take
  a capability that would normally threaten the safety boundary and reframe it so
  it *structurally cannot*. (I applied this directly in the v2.0 spine: a society
  shares cognition as data, so R1 protects the noosphere at birth — learned here.)

- **Find the generative principle, not the feature list.** *The v2.0 spine
  derives four phases from one through-line (complete the I/O triad → prove →
  package → live → connect), reusing existing machinery pointed at new targets:
  TrustMatrix → inter-instance trust; antonym-axis reconciliation →
  cross-instance contradiction.* — Features should fall out of a principle, not
  accumulate.

- **Catch the subtle interaction bug at design time, in prose.** *F2 #1:
  probation grants must be idempotent-per-epoch, otherwise the breaker consulting
  `max_action_retries()` to compute its threshold would "burn" the probe and read
  0 → immediate false trip. Caught in the spec, before any code.* — Simulate the
  collaborators' interactions mentally and find the race/ordering bug before it
  exists.

- **New code rhymes with existing code.** *F4 §3.2: `goal_text` is ALTER-migrated
  "in the pattern already used for `verdict_reasons` (try/except
  OperationalError)."* — Reuse the established idiom instead of inventing a parallel one.

---

## The through-line

Across all six axes, one disposition recurs: **make the reasoning auditable and
the claims falsifiable.** Decisions are tagged, deviations are justified against
the spec's intent, numbers carry their own limits, premises are checked before
they're obeyed, and every safety property is re-stated and attacked rather than
assumed. The intelligence isn't ornamental — it shows up as *fewer ways to be
wrong*: power reframed as data, bugs caught in prose, gates that can actually
fail.

The one pattern already carried forward into v2.0: *everything-is-data → the
safety invariant scales for free.* The rest of this list is the standard to hold
the v1.2 → v2.0 work to.

---

## Cross-reference: official Claude 4.8 / adaptive-thinking docs

Checked against the two official sources (`platform.claude.com/docs`):
*Novidades no Claude Opus 4.8* and *Pensamento adaptativo*. What the model docs
ground in the patterns above:

- **"Adaptive thinking" is a real, documented model capability**, not a metaphor.
  It is the request mode `thinking: {type: "adaptive"}` — the model itself
  decides *when* and *how much* to think and auto-interleaves reasoning between
  tool calls; depth is governed by `output_config.effort`
  (`low|medium|high|xhigh|max`). On **Opus 4.7/4.8 and Fable 5 it is the only
  thinking mode** (manual `budget_tokens` → 400); on Fable 5/Mythos 5 it is
  *always on*. So the "adaptive thinking" axis of this study isn't an analogy to
  the corpus — it is the literal mechanism the corpus's author runs on.
- **The behavioral shifts Anthropic documents for 4.8 mirror the patterns
  observed here.** The official 4.8 guidance flags: more deliberate (asks more,
  weighs options), calibrates verbosity to task complexity, narrates more, and
  must be told to *ground progress claims against tool results* and *report
  outcomes faithfully*. That "ground claims / report faithfully" instruction is
  the same disposition extracted under **§5 Honesty** — the model is steered
  toward auditable, falsifiable output, and the corpus shows what that looks like
  when applied to planning artifacts.
- **Effort is the rigor lever.** The depth and verification behavior that make
  §3 Meta-cognition and §4 Reliability possible are bought with `effort` — 4.8
  defaults to `high`; this project's global config runs `xhigh`. The corpus's
  exhaustive self-review + risk tables are the kind of output high effort
  produces and low effort skips.
- **Config note (verified, 2026-06-13):** adaptive thinking has **no
  `settings.json` switch** — it is intrinsic to the model on 4.7/4.8/Fable 5.
  The global Claude config (`~/.claude/settings.json`) already carries
  `model: "opus"` (→ Opus 4.8, adaptive-only) and `effortLevel: "xhigh"`, so it
  is already enabled and tuned above default. Nothing was added; inventing an
  `adaptiveThinking` key would be exactly the kind of theater §5 warns against.
  4.8 also ships `speed: "fast"` (research-preview fast mode) and a lower
  prompt-cache minimum (1024 tokens) — neither relevant to this study.
