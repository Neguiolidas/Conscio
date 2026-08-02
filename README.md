<p align="center">
  <img src="https://raw.githubusercontent.com/Neguiolidas/Conscio/main/docs/assets/conscio-banner.webp" alt="Conscio — a self-awareness framework for AI agents" width="820">
</p>

<p align="center">
  <b>Context-aware memory, introspection, goal generation, and an audited agency
layer that lets a model act on its own conclusions under hard safety gates.</b>
</p>

> *"The first step toward consciousness is knowing what you are and what limits you."*

Conscio runs **local-first** and **zero-dep at the core** (`numpy` + stdlib `sqlite3`,
nothing else). It is built to make small, local models punch above their size — by
giving them memory, self-judgment, and procedural skill — and to prove that claim by
measurement, not assertion.

**Latest release — `v3.9.5` "Latch and Release":** a global lockdown is no
longer able to outlive the circuit breaker that raised it, so a daemon that once
hit quorum stops being paralysed forever; the failure-rate brake is reported for
the heartbeat it belongs to instead of as permanent status; every path written
with a `~` — `storage_path`, `HERMES_HOME`, `CONSCIO_SESSION_DB`,
`CONSCIO_VAULT_DIR`, `CLAUDE_DIR` and eleven other env vars — resolves to the
directory the caller meant rather than one named `~` in the working directory;
and `conscio.feed` / `conscio.note` no longer crash the MCP server when the host
sends `data` instead of `payload` — a normalization layer maps the aliases
before validation, leaving canonical events untouched.

> Full version history: [**CHANGELOG.md**](CHANGELOG.md).

---

## Install

```bash
pip install conscio          # from PyPI
conscio init                 # wizard: bind this host to its own space

pip install -e ".[dev]"      # from source, with the dev toolchain
pip install "conscio[docs]"  # to build the docs site (mkdocs-material)
```

Requires Python ≥ 3.10. The core depends only on `numpy` (`sqlite3` is stdlib) and is
typed (PEP 561). The wheel ships console scripts `conscio`, `conscio-mcp`,
`conscio-daemon`, `conscio-hub`, `conscio-observatory`, `conscio-bench`. `dev`/`docs`
extras never enter the runtime import graph.

## Quick start

```python
from conscio import ConsciousnessEngine

# Passive consciousness — auto-detects model and mode
with ConsciousnessEngine(model_name="glm-5.2") as engine:
    result = engine.reflect(
        world_state="All systems operational",
        confidence=0.8,
        anomalies=["Unusual latency spike detected"],
    )
    injection = engine.get_state_for_injection()  # compact state for context injection
    engine.world.add_entity("server", "system", state="healthy")
    hits = engine.recall("latency incidents")     # cross-session memory (FTS5 + optional RAG/vector)

    # Self-evaluation — 5-axis rubric, deterministic, no LLM
    report = engine.evaluate()
    print(report.overall, report.self_check)

    # Gate tools
    adr = engine.decide(title="Use SQLite for session storage", status="proposed")
    result = engine.council("Should we enable autonomous mode?")
    gate = engine.loop_gate(task="nightly audit", frequency="daily",
                            verifiable=True, budget_ok=True, has_tools=True)
    check = engine.delivery_check()
    evidence = engine.investigate(target="server latency")
```

Arithmetic a model would otherwise guess at is evaluated, not generated — see
[Intercepter](#intercepter):

```python
from conscio.agency.intercepter import Intercepter

itc = Intercepter()
itc.process("[INTERCEPT: solve_linear(2, 3, 1, 7)]").text
# '[INTERCEPT: solve_linear(2, 3, 1, 7)] -> [RESULT: 4.0]'
```

```bash
conscio init                  # bind this host to its own space (wizard)
conscio info                  # model context window / mode / budget
conscio reflect "System health check" --mode minimal
conscio council "Should I deploy to production?" --mode compact
conscio search "latency" --k 5
conscio ingest file.md        # feed documents into episodic memory
conscio plugins               # what adapters/sensors/tools are installed
conscio manual                # where the full usage manual lives
```

`reflect` is the **passive heart** and is never allowed to act. Everything that can
change the world lives behind `act` and its safety gates — a separation that is
non-negotiable (see [Safety rules](#safety-rules-non-negotiable)).

---

## When to use Conscio (MCP trigger rules)

Conscio is a cognitive refinement layer, not a fact database. Calling it on every
message wastes tokens and adds latency.

**Call Conscio when the cost of being wrong is high:**

- Security audit → `feed` + `cognitive_cycle`
- Architectural decision → `decide` or `council`
- Debugging → `investigate`
- Multi-step delivery → `loop_gate` + `delivery_check`
- Self-review of output → `evaluate` (5-axis rubric)
- High-risk irreversible action → `council`

**Do NOT call Conscio for** factual lookup, casual conversation, simple mechanical
tasks, one-shot tool calls, or anything with no decision or judgment involved.

**Decision rule:** cost of reversal. Cheap to undo → skip Conscio. Expensive to undo
→ Conscio pays for itself.

See [USAGE.md](USAGE.md#when-to-call-conscio-mcp-trigger-rules) for the full table.

---

## What Conscio does

- **Knows itself** — detects its model and context window (offline & deterministic by
  default; opt-in auto-detection), and adapts its footprint.
- **Reflects continuously** — a passive inner-monologue loop that observes, assesses
  confidence, and summarizes (`engine.reflect` — advisory, never acts). Reflection
  depth adapts via ReflectionGate.
- **Generates its own goals** — driven by curiosity, maintenance, and evolution.
- **Acts under audit** — an opt-in agency layer (`engine.act`) that proposes,
  audits, risk-gates, and only then executes — with a human gate for anything risky.
- **Learns procedures** — successful audited plans become reusable skills (procedural
  memory), fed back to the actor as few-shot exemplars.
- **Judges its own quality** — confidence calibration, blind-spot detection, and
  coherence/dissonance metrics that name the dimensions they could not measure
  rather than scoring them silently; formal self-evaluation (`evaluate`).
- **Gates its own decisions** — ADRs (`decide`), multi-voice council
  (`council`), autonomous-loop gate (`loop_gate`), pre-close delivery check
  (`delivery_check`), and read-before-act verification (`investigate`).
- **Pipelines its own work** — intent-driven acceptance criteria, post-
  implementation verification, loop-pattern selection, strategic compaction
  advisory, and a recursive decision ledger with promotion gates.
- **Diagnoses its own context** — context-budget audit, eval harness with
  pass@k reliability metrics, and rule distillation from skills/events/decisions.
- **Stores & retrieves knowledge** — FTS5 BM25 dual-index with RRF merging; optional
  semantic recall; KnowledgeGraph with entities, triples, and timeline.
- **Semantic chunking + vector search** — `ContentStore` splits by heading
  (markdown), `---` boundary (yaml), or paragraph (everything else); auto-detect
  embedding pipeline via sentence_transformers + `VectorBackend` (batched numpy
  cosine search) fused into `recall()` via `HybridRetriever` (RRF, lexical + dense).
  Override with `CONSCIO_VECTORS=0` to disable. `conscio ingest <path>` bulk-indexes
  a directory.
- **Organizes memory in wings and rooms** — Hallways hierarchy: wing → room →
  drawer, with auto-created defaults and FK enforcement; WingManager integrates
  Hallways + ContentStore for filtered search.
- **Ingests files and conversations** — Miner: .md/.txt/.jsonl ingestion with
  paragraph splitting, conversation JSONL parsing, directory walking with skip dirs.
- **Detects entities** — EntityDetector: regex Unicode (PT accents), detects
  persons, domains, versions; stores in KnowledgeGraph.
- **Embeds natively** — EmbeddingProvider with 3-tier fallback: Ollama →
  OpenAI-compatible → sentence_transformers all-MiniLM-L6-v2 (384-dim, in-process,
  no daemon). Optional 768-dim via `CONSCIO_EMBED_MODEL=nomic-embed-text-v1.5`.
- **Remembers what its tools saw** — every tool call the host makes is captured
  into a separate `obs.db` and searchable later at zero LLM tokens, so a
  compaction stops costing you the work that preceded it.
- **Governs its own context cost** — measures the stable prefix and where
  compactions actually land, derives the cost-optimal window, and reports
  current-vs-baseline priced per turn from the host's own usage records.
- **Exports & imports** — tar.gz archive with ContentStore + KG + Hallways +
  metadata.json; MemPalace ChromaDB adapter (import_format_mempalace).
- **Judges output quality** — an optional 6th evaluation axis, `output_quality`
  (LLM-as-judge with heuristic fallback). The overall score averages over the
  axes actually active, so enabling it never silently reweights the other five.
- **Consolidates while idle** — a dream cycle that releases, prunes, reconciles,
  crystallizes, and distills.
- **Persists across sessions** — heartbeat/handoff continuity with on-demand injection.
- **Knows its codebase structurally** — optional, consent-gated ingestion of a
  Graphify graph, distilled to a compact signal injected budget-aware. Data, never
  code (R10).
- **Computes instead of guessing** — Intercepter evaluates `[INTERCEPT: ...]`
  expressions a model emits with a restricted AST walker, and feeds the real
  answer back to it.
- **Plugs into any host** — a stdlib-only MCP stdio server (`conscio-mcp`) feeds any
  CLI/IDE/agent its cognition and audited proposals live.

---

## Safety rules (non-negotiable)

1. **No autonomous self-modification** — evolution proposals require human approval.
2. **Context injection has hard limits** — never exceeds the mode budget.
3. **Goals never execute directly** — only through the audited `act` pipeline
   (output contract + Skeptic audit + risk gating + earned autonomy + circuit breaker).
4. **Reflections are append-only** — never edited once written.
5. **Cannot modify its own safety rules** — no self-referential gate bypass.
6. **HIGH-risk actions always require human approval** — never auto-executed.
7. **No network in the tool registry** — the only network the core may touch is the
   InferenceAdapter (localhost by default).
8. **Every external effect goes through the ActionLedger** — append-only, auditable.
9. **Autonomous operation requires Awake Mode (R9)** — the self-initiated heartbeat
   only acts when the persisted `awake` flag is on; **default OFF**. Asleep, it
   perceives and `reflect`s only. A human's direct `engine.act` is not gated by R9.
10. **Imported cognition is data, never code (R10)** — a code graph, a shared
    skill, or anything else that arrives from outside is parsed, never evaluated,
    and re-audited locally. No `eval`/`exec`/`pickle`, no runtime dependency on
    the tool that produced it, and a code-looking label is returned verbatim.

---

## Context-aware modes

Conscio detects the model's context window and adapts how much "consciousness state"
it injects. The mode governs **injection budget only** — never whether the framework
runs (it runs from 8k context up).

| Mode | Context window | Injection budget | What's injected |
|---|---|---|---|
| **Minimal** | < 128k | ≤ 200 tokens | Off-context everything; on-demand retrieval |
| **Compact** | 128k–256k | ≤ 500 tokens | Summary + last reflection + top goals |
| **Standard** (recommended) | 256k+ | ≤ 1000 tokens | Full state; world subgraph; self-assessment |

---

## Capabilities

### Audited agency

```python
from conscio.agency import OllamaAdapter

engine.attach_adapter(OllamaAdapter(model="qwen3.5:0.8b"))  # or a frontier API
report = engine.act()                # downstream of reflect; proposes only (L1)
if report.status.value == "proposed":
    engine.approve(report.ledger_id)  # the human gate executes it

engine.probe()                       # lazy, empirical capability measurement
engine.run(budget=...)               # L3 heartbeat — asleep (default) it only reflects
```

Autonomy is **earned and measured**, never assumed: `ProbeSuite` measures the attached
model, `TrustMatrix` grants L1/L2/L3 from real calibration and ledger history, and the
`CircuitBreaker` quarantines misbehaving goals. HIGH-risk actions are *always* queued
for a human (R6).

### Gate tools

Five advisory tools for decision governance — all deterministic, EventBus-backed, no
LLM calls:

```python
# Architecture Decision Records
adr = engine.decide(title="Use SQLite for session storage", status="proposed")
# {"adr_id": "ADR-20260802145940-48a11e", "title": "...", "status": "proposed", ...}

# Multi-voice council (Architect + Skeptic + Pragmatist + Critic)
result = engine.council("Should we enable autonomous mode?")
# {"question": "...", "recommendation": "proceed", "voices": [...], "votes_summary": {...}}

# Autonomous loop gate — every condition must pass, and an unstated one is a veto
gate = engine.loop_gate(task="nightly audit", frequency="daily",
                        verifiable=True, budget_ok=True, has_tools=True)
# {"approved": True, "conditions": {...}, "vetoed_conditions": []}

# Pre-close delivery check (auto-runs on engine.close())
check = engine.delivery_check()
# {"pass": True, "blockers": [], "rationalization_hits": 0, "stale_proposals": 0, ...}

# Read-before-act evidence verification
evidence = engine.investigate(target="server latency")
# {"satisfied": False, "missing": ["investigate:read: server latency"], ...}
```

`loop_gate` fails closed: leave `frequency` empty and it vetoes on that alone.
Same for `investigate` — `satisfied` is False until the EventBus actually holds a
read of that target, so "no evidence" never reads as "verified".

### Pipeline tools

Five tools for structured workflows — acceptance criteria, verification, loop
patterns, compaction advisory, and recursive decision ledger:

```python
# Intent-driven acceptance criteria with auto risk detection
criteria = engine.acceptance_criteria(goal="Deploy to production", depth="full")
# {"goal": "...", "risk_level": "low", "risk_domains": [], "acceptance_count": 6, "criteria": [...]}

# Post-implementation verification against the criteria last raised
verified = engine.verify(criteria_source="acceptance")
# {"pass": False, "verified": [], "failed": [{"id": "AC-001", "reason": "no evidence found"}, ...]}

# Loop pattern selection (sequential / continuous_pr / rfc_dag / infinite)
loop = engine.continuous_loop(pattern="continuous_pr")

# Strategic compaction advisory
compact = engine.strategic_compact(context_tokens=8000, context_window=128000)

# Recursive decision ledger with promotion gates (paper → dry_run → live)
entry = engine.ledger(action="record", rollout_id="RL-1",
                      candidates=[{"id": "A", "description": "A"}],
                      marks={"A": "accept"})
promoted = engine.ledger(action="promote", rollout_id="RL-1")
```

### Diagnostic tools

Three tools for context auditing, evaluation, and rule extraction:

```python
# Context budget audit — token pressure, metabolic tiers, recommendations
budget = engine.context_budget(context_tokens=8000, context_window=128000)
# {"token_pressure": ..., "headroom_pct": ..., "metabolic_tiers": [...], "recommendations": [...]}

# Eval harness with pass@k reliability metrics
defined = engine.eval_harness(action="define", eval_type="capability",
                              task="memory recall", criteria=["recalls the goal"])
engine.eval_harness(action="run", eval_id=defined["eval_id"], results=[True, True, False])
report = engine.eval_harness(action="report")

# Rule distillation — scan for recurring patterns, then commit one as a rule
rules = engine.rules_distill(action="scan", source_types=["skills", "events"])
distilled = engine.rules_distill(action="distill", rule_text="Always verify before acting")
```

### Self-evaluation

Formal 5-axis rubric — accuracy, completeness, clarity, actionability, conciseness
(a 6th, `output_quality`, joins them when an output is passed). Pure read-only,
deterministic, no LLM:

```python
report = engine.evaluate()
report.overall        # 4.2 — mean of the axes actually active
report.axes           # (AxisScore(axis="accuracy", score=4, evidence=..., improvement=...), ...)
report.self_check     # "User might ask for follow-up on weaker axes"
report.improvements   # ("Raise confidence by adding verification steps for claims.", ...)
```

The scores are read off the engine's real state, so a fresh instance scores
lower than a working one and the improvements name what is actually missing —
they are measurements, not a fixed rubric printout.

### Tool observations & context economy

Every tool call a session makes is recorded in its own SQLite store (`obs.db`,
separate from `conscio.db`), searchable later at **0 LLM tokens** — so a smaller
context window stops meaning lost work. On Claude Code the installer wires this
up automatically; the capture never alters tool output and never blocks a
session.

```python
engine.observe(tool="Bash", input_text="ls", output_text="README.md", session_id="s1")
hits = engine.recall_observations("README", session_id="s1")  # FTS5 snippet window
handoff = engine.compress_observations(session_id="s1")       # session → handoff
```

Recall is session-scoped by default, so a session only ever mines its own trail
unless you widen `scope`. Under the Claude Code hook the session id is supplied
for you.

```bash
conscio govern status    # ceiling, obs.db size, capture health, baseline
conscio govern prefix    # measure your stable prefix and where compactions land
conscio govern on        # freeze a baseline + apply the cost-optimal window here
conscio govern report    # current vs baseline, from the host's own usage records
conscio govern off       # restore what you had before
```

`govern report` prices both sides per turn rather than as totals — a total
against a total mostly measures which side ran longer. Where the baseline froze
no figure to compare against, the cell prints `—` instead of a zero that would
render as a 100% saving.

> Capture is complete, not truncated: a tool's whole input and output are stored
> (up to 1 MiB per field), so anything a tool reads or writes — including
> secrets — can land in `obs.db`. It is a second copy of what the host already
> keeps in its session transcripts, with a 30-day retention window. Treat it as
> one more place to scrub, and lower `max_age_days` if that matters to you.

### Live mode — daemon, sensors & Awake Mode

Conscio can run as a **living process** that perceives the world each cycle and acts
**only when explicitly awake** (R9, default OFF):

```python
from conscio import ConsciousnessEngine, HostSensor
from conscio.daemon import Daemon

engine = ConsciousnessEngine("glm-5.1", storage_path="~/.conscio/live")
engine.wake()                                             # opt in to autonomy (persisted)
Daemon(engine, sensors=[HostSensor()], interval=30).run()  # perceive → reflect → act
```

`storage_path` accepts `str` or `Path` and expands `~`.

`conscio-daemon --sensors host --interval 30` runs it standalone (add `--awake` to
enable autonomy); `conscio awake` reaches an already-running daemon rather than
waking a second engine of its own. Reference sensors `HostSensor` / `AgentSensor` ship as
`conscio.sensors` entry points; write your own `SensorAdapter`.

### Structural cognition

Conscio can give the model **structural awareness of the codebase it works in**,
distilled from a Graphify-format `graph.json` — consumed as **data, never code** (R10:
no `networkx`, no Graphify runtime dependency). Consent is per-workspace and defaults
OFF; it tracks drift + staleness vs the repo `HEAD` (read purely from `.git`, no
subprocess). See [the integration guide](docs/guides/integration.md#structural-cognition).

### Embodiment — MCP server

`conscio-mcp` is a hand-rolled, **stdlib-only** MCP stdio server (newline-delimited
JSON-RPC 2.0) so any MCP host can plug into a Conscio instance and consume its
cognition live. Zero new dependency; nothing opens a socket. The base surface is
**propose-only** (perceive / reflect / recall / audit); opt-in `--enable-act` adds
host-executed, ledgered, gated `act` — Conscio signs and audits the intent, the host
pulls the trigger. 13 additional MCP tools for gate, pipeline, and diagnostic
operations. See [the MCP guide](docs/guides/mcp.md).

### Society — shared minds

Same-host instances can **share locally-proven skills as data** through a host-shared
`noosphere.db` (publish → static-revalidated quarantine → sandboxed trial →
promotion), **audit each other's** action records, and exchange messages over the
Liaison mailbox (`hermes_review` cross-agent approvals + free-form relay). Engine-free,
read-only on the live `conscio.db`, no inherited trust, no network.

### Intercepter

A model asked for `0.15 * 8000` will happily invent a number. Intercepter takes the
`[INTERCEPT: ...]` expressions it emits and *evaluates* them — a restricted AST
walker over arithmetic, comparisons, a fixed set of math functions (`sqrt`, `floor`,
`log`, the trig family, `solve_linear`) and bound variables. No `eval`, no `exec`,
no attribute access, no imports; expressions are length- and depth-capped.

```python
from conscio.agency.intercepter import Intercepter

itc = Intercepter()
itc.set_variable("rate", 0.15)
itc.process("[INTERCEPT: rate * 8000]").text
# '[INTERCEPT: rate * 8000] -> [RESULT: 1200.0]'
```

`attach_adapter(..., intercept_enabled=True)` wires it into the act pipeline as an
`InterceptionLoop` around the inference adapter: the model emits a tag, gets the real
answer back, and may revise — up to 3 iterations, each one an EventBus record.
Origin: the Think-Vetor DSL concept (CromIA), reimplemented from scratch.

---

## Architecture

```
            reflect  ── passive · advisory · append-only ──┐
                                                              │
  ConsciousnessEngine  (orchestrator · lifecycle · injection) │
   ├─ Witness        InnerMonologue · WorldModel · MetaCognition · GoalGenerator
   ├─ Substrate      ContentStore (FTS5 BM25 + RRF) · VectorBackend + HybridRetriever (opt-in) · EventBus (41 event types) · FilterPipeline
   ├─ Continuity     SessionLifecycle (6-step handoff) · SessionRAG (optional)
   ├─ Metabolism     MetabolicContext · DreamCycle (release→prune→…→distill)
   ├─ Coherence      CoherenceEngine · semantic reconciliation · unmeasured dimensions named
   ├─ Structural     StructuralDistiller (graph → ranked signal; data, not code)
   ├─ Evaluation     evaluate — 5/6-axis rubric (accuracy·completeness·clarity·
   │                 actionability·conciseness·output_quality)
   ├─ Gates          decide · council · loop_gate · delivery_check · investigate
   ├─ Pipelines      acceptance_criteria · verify · continuous_loop ·
   │                 strategic_compact · ledger
   ├─ Diagnostics    context_budget · eval_harness · rules_distill
   ├─ Harness        PromptZones (stable+volatile) · CheckpointChain ·
   │                 TokenAccount+CPM · FailureGovernor (6-type) ·
   │                 adaptive max_retries · skeptic skip (safe tools)
   ├─ Adaptive       prompt_complexity (full/compact/minimal) ·
   │                 auto-detect (--model auto) · FallbackAdapter
   ├─ Memory         KnowledgeGraph · Hallways · WingManager · VectorBackend ·
   │                 Deduplicator · EntityDetector · EmbeddingProvider ·
   │                 Miner · Migration (export/import tar.gz)
   ├─ Observations   obs.db (FTS5, separate from conscio.db) · capture hooks ·
   │                 recall_observations · compress_observations
   ├─ Governor       prefix/landing measurement → cost-optimal window · baseline
   │                 + report, priced per turn from the host's usage records
   ├─ Intercepter    restricted-AST evaluation of [INTERCEPT: ...] tags
   └─ Embodiment     conscio-mcp: JSON-RPC 2.0 over stdio (stdlib only)
                                                              │
            act  ── opt-in agency · audited · gated ◀───────┘
              Skeptic (hostile audit) · TrustMatrix (earned autonomy) ·
              CircuitBreaker (per-goal quarantine) · ActionLedger (append-only)
```

Subsystem detail and the full public-API reference live on the **docs site** (`docs/`,
built with `mkdocs build --strict`).

---

## Any model

Conscio is **model-agnostic** — it runs on any backend (local Ollama / llama.cpp /
LM Studio, any OpenAI-compatible endpoint, or a frontier API). The only thing it needs
from a model is its **context window**: that single number selects the injection mode
(see [Context-aware modes](#context-aware-modes)) and nothing else is hardcoded to a
particular model.

A known model resolves to its window offline and deterministically; an unknown one is
inferred from its name or taken from an explicit override. Register any model — or pin
a window — in one line:

```python
from conscio import ModelRegistry
ModelRegistry.register("my-model", context_window=200_000)
```

---

## Model-agnostic by design

Conscio adapts to any model — from 0.8B local to frontier API — using two
mechanisms:

### Adaptive prompt complexity

The `ProbeSuite` measures each model's `json_fidelity`, `schema_depth`, and
`instruction_depth` (5 empirical probes, cached in SQLite). Based on the
profile, `prompt_complexity` selects one of three prompt tiers:

| Tier | Persona | Tools | State | Memories | Few-shot | When |
|------|---------|-------|-------|----------|----------|------|
| `full` | complete | ✓ | ✓ | ✓ | ✓ | json_fidelity ≥ 0.8 + instruction_depth ≥ 2 |
| `compact` | 1-line | ✓ | ✓ | ✗ | ✗ | instruction_depth ≥ 2 + schema_depth ≥ 2 |
| `minimal` | none | ✓ | ✓ | ✗ | ✗ | otherwise (tiny models) |

The bench loop tries `full` first and falls back to `compact` if args
validation fails — so models with identical profiles but opposite
preferences (Qwen 0.8B wants full, LFM 1.2B wants compact) both hit 100%.

### Auto-detect + fallback chain

`--model auto` makes the MCP JSON **fixed forever** — no manual model
swapping when you change what's loaded in LM Studio:

```json
{
  "mcpServers": {
    "conscio": {
      "command": "conscio-mcp",
      "args": ["--model", "auto", "--base-url", "http://localhost:1234/v1"]
    }
  }
}
```

On boot, Conscio `GET /v1/models`, filters out embedding models, tests each
chat model with a minimal prompt, and uses the first that responds. The
winner is persisted to `~/.config/conscio/config.json` so the next boot
starts instantly. At runtime, `FallbackAdapter` switches to the next model
in the chain if the current one fails (PERMANENT error, timeout, bad
response).

### Benchmark (local LM Studio, 5 cycles)

| Model | json_fidelity | Tier | JSON valid | Tokens | Latency p50 | Catch rate |
|-------|--------------|------|------------|--------|-------------|------------|
| Qwen 0.8B | 1.0 | T2 | **100%** | 5357 | 19.0s | 100% |
| LFM 1.2B | 1.0 | T2 | **100%** | 4950 | 23.6s | 100% |

Both small models hit 100% JSON validity through Conscio (raw: 60% and 80%).
Token cost 6–10× (no prompt caching on local LM Studio); with provider
caching (Anthropic/OpenAI), the stable zone caches at ~0.1×, reducing
effective cost to ~2–3×.

---

## Bench

```bash
conscio-bench --adapter mock                          # offline, deterministic
conscio-bench --adapter ollama:qwen3.5:0.8b --cycles 20
conscio-bench --adapter mock --skills 20              # skill-acquisition curve
```

Also runnable from a source checkout as `python3 -m conscio.bench`. Backends:
`mock`, `ollama:<model>`, `llamacpp[:<name>]`, `lmstudio:<model>[@<base_url>]`,
`openai:<model>[@<base_url>]`.

Reports probe profile, decode tier, per-tier syntactic validity, Skeptic catch-rate,
latency p50, and calibration. Baselines in `docs/bench/`.

---

## Extending Conscio

Three stable extension points, usable directly or published by a third party and
auto-discovered via entry points (`conscio.adapters` / `conscio.sensors` /
`conscio.tools`):

```toml
# in your own package's pyproject.toml
[project.entry-points."conscio.sensors"]
my-sensor = "my_pkg:MySensor"        # a conscio.perception.SensorAdapter
```

Runnable examples: `examples/custom_adapter.py`, `examples/host_guardian.py`,
`examples/agent_companion.py`. Discover what is installed with `conscio plugins`.

---

## Testing & data

```bash
# House rule: one file per pytest process (low-RAM machines OOM on the full run; CI matches)
for f in tests/test_*.py; do pytest "$f" -q; done
pytest tests/test_agency_act.py -v    # a specific module
```

SQLite in WAL mode. The engine's storage defaults to `~/.hermes/consciousness/`,
where `conscio.db` holds ContentStore + EventBus + ActionLedger + skills and
`obs.db` holds tool observations in a store of its own. Pass `storage_path=` (or
`--storage`) to move it; the CLI and daemon additionally honour `$HERMES_HOME`,
which the library default does not read. Cross-instance state — the knowledge
graph, hallways, vectors, dedup, handoffs, the act sandbox — lives under
`~/.conscio/`. **Always** call `engine.close()` or use the `with` statement so WAL
checkpoints flush. Session continuity writes a compact heartbeat (`<1.5KB`,
auto-injected next session) plus a richer handoff and dated archives.

---

## License

AGPL-3.0-or-later — Neguiolidas / Neguitech
