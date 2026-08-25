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

**Latest release — `v4.3.1` "Private-cursor relay sweep":** new
`conscio.liaison.tick` module — private-cursor relay sweep + IMPORTANT
classification for host supervisors (systemd/cron), fixing the multi-agent
cursor race in the shared-mailbox relay. See [CHANGELOG](CHANGELOG.md).
council now resolves ambiguous/split decisions to `hold` (never a silent
`proceed`), a clean critic votes `proceed` honestly, and each voice degrades
gracefully instead of crashing the whole council. New `consensus_strength`
and `dissenting_voices` fields. Previous — `v4.2.0` "A2A agent society
(native)": a native A2A
relay watchdog (`conscio/liaison/watcher.py`) replaces the external
`relay_watch_hermes.py`, with a per-peer cursor and honest exit codes; a
delta transcript cuts ~70-90% of prompt tokens in relay auto-reply
(`--delta-no-history`); and an `agents` table + capability routing let the
emitter target the right peer by tag. `4.1.0` made every MCP tool
`conscio_recall` (a dot is legal in MCP but rejected by Anthropic/OpenAI
function-name rules — one connects, reports success, and silently disables
every tool served). `4.0.x` made Conscio installable as a Claude Code plugin
and gave its MCP surface three sizes — `lite` (10) / `balanced` (18) / `ultra`
(35), switchable at runtime with `conscio_mode`.

> Full version history: [**CHANGELOG.md**](CHANGELOG.md).

---

## Install

**As a Claude Code plugin** — memory, capture hooks and 14 slash commands, with no
Python toolchain to manage:

```
/plugin marketplace add Neguiolidas/Conscio
/plugin install conscio
```

**As a library or CLI:**

```bash
pip install conscio          # from PyPI
conscio init                 # wizard: bind this host to its own space

pip install -e ".[dev]"      # from source, with the dev toolchain
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

    report = engine.evaluate()                    # 5-axis self-evaluation, no LLM
    adr = engine.decide(title="Use SQLite for session storage", status="proposed")
    verdict = engine.council("Should we enable autonomous mode?")
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

## When to use Conscio

Conscio is a cognitive refinement layer, not a fact database. Calling it on every
message wastes tokens and adds latency.

**Call Conscio when the cost of being wrong is high:**

| Situation | Tool |
|---|---|
| Security audit | `feed` + `cognitive_cycle` |
| Architectural decision | `decide` or `council` |
| Debugging | `investigate` |
| Multi-step delivery | `loop_gate` + `delivery_check` |
| Self-review of output | `evaluate` |
| High-risk irreversible action | `council` |

**Do NOT call Conscio for** factual lookup, casual conversation, simple mechanical
tasks, one-shot tool calls, or anything with no decision or judgment involved.

**Decision rule:** cost of reversal. Cheap to undo → skip Conscio. Expensive to undo
→ Conscio pays for itself.

Full trigger table: [USAGE.md](USAGE.md#when-to-call-conscio-mcp-trigger-rules).

---

## What Conscio does

- **Knows itself** — detects its model and context window (offline & deterministic by
  default) and adapts its injection footprint.
- **Reflects continuously** — a passive inner-monologue loop that observes, assesses
  confidence, and summarizes (`engine.reflect` — advisory, never acts), at a depth
  ReflectionGate adapts.
- **Generates its own goals**, driven by curiosity, maintenance, and evolution.
- **Acts under audit** — an opt-in agency layer (`engine.act`) that proposes, audits,
  risk-gates, and only then executes, with a human gate for anything risky.
- **Learns procedures** — successful audited plans become reusable skills, fed back to
  the actor as few-shot exemplars.
- **Judges its own quality** — confidence calibration, blind-spot detection, and
  coherence metrics that *name the dimensions they could not measure* rather than
  scoring them silently.
- **Governs its own decisions** — ADRs, a four-voice council, an autonomous-loop gate,
  a pre-close delivery check, and read-before-act verification.
- **Stores & retrieves knowledge** — FTS5 BM25 dual-index with RRF merging, optional
  semantic recall, and a KnowledgeGraph with entities, triples and timeline.
- **Searches semantically** — `ContentStore` chunks by heading/boundary/paragraph and
  `HybridRetriever` fuses lexical and dense results into `recall()`. Three vector
  backends with auto-detect (see [below](#vector-backends)). `conscio ingest <path>`
  bulk-indexes a directory.
- **Organizes memory in wings and rooms** — a wing → room → drawer hierarchy with FK
  enforcement and filtered search.
- **Embeds natively** — a 3-tier fallback: Ollama → OpenAI-compatible →
  sentence-transformers all-MiniLM-L6-v2 (384-dim, in-process, no daemon).
- **Remembers what its tools saw** — every tool call the host makes is captured into a
  separate `obs.db` and searchable later at zero LLM tokens, so a compaction stops
  costing you the work that preceded it.
- **Governs its own context cost** — measures the stable prefix and where compactions
  actually land, derives the cost-optimal window, and reports current-vs-baseline
  priced per turn from the host's own usage records.
- **Consolidates while idle** — a dream cycle that releases, prunes, reconciles,
  crystallizes, and distills — and persists across sessions via heartbeat/handoff.
- **Knows its codebase structurally** — optional, consent-gated ingestion of a Graphify
  graph, distilled to a compact signal. Data, never code (R10).
- **Computes instead of guessing** — the Intercepter evaluates `[INTERCEPT: ...]`
  expressions with a restricted AST walker and feeds the real answer back.
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
10. **Imported cognition is data, never code (R10)** — a code graph, a shared skill, or
    anything else arriving from outside is parsed, never evaluated, and re-audited
    locally. No `eval`/`exec`/`pickle`, and a code-looking label is returned verbatim.

---

## Context-aware modes

Conscio detects the model's context window and adapts how much state it injects. The
mode governs **injection budget only** — never whether the framework runs (it runs from
8k context up).

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
report = engine.act()                 # downstream of reflect; proposes only (L1)
if report.status.value == "proposed":
    engine.approve(report.ledger_id)  # the human gate executes it

engine.probe()                        # lazy, empirical capability measurement
engine.run(budget=...)                # L3 heartbeat — asleep (default) it only reflects
```

Autonomy is **earned and measured**, never assumed: `ProbeSuite` measures the attached
model, `TrustMatrix` grants L1/L2/L3 from real calibration and ledger history, and the
`CircuitBreaker` quarantines misbehaving goals. HIGH-risk actions are *always* queued
for a human (R6).

### Gates, pipelines and diagnostics

Thirteen deterministic, EventBus-backed tools — no LLM calls:

| Group | Tools |
|---|---|
| **Gates** | `decide` (ADRs) · `council` (Architect + Skeptic + Pragmatist + Critic) · `loop_gate` · `delivery_check` · `investigate` |
| **Pipelines** | `acceptance_criteria` · `verify` · `continuous_loop` · `strategic_compact` · `ledger` (paper → dry_run → live) |
| **Diagnostics** | `context_budget` · `eval_harness` (pass@k) · `rules_distill` |

They fail closed. Leave `loop_gate`'s `frequency` empty and it vetoes on that alone;
`investigate` reports `satisfied: False` until the EventBus actually holds a read of
that target, so "no evidence" never reads as "verified". Signatures and return shapes:
[USAGE.md](USAGE.md).

### Self-evaluation

`engine.evaluate()` returns a formal 5-axis rubric — accuracy, completeness, clarity,
actionability, conciseness (a 6th, `output_quality`, joins them when an output is
passed). Read-only, deterministic, no LLM. Scores are read off the engine's real state,
so a fresh instance scores lower than a working one and the improvements name what is
actually missing — measurements, not a fixed rubric printout.

### Tool observations & context economy

Every tool call a session makes is recorded in its own SQLite store (`obs.db`, separate
from `conscio.db`), searchable later at **0 LLM tokens** — so a smaller context window
stops meaning lost work. On Claude Code the plugin wires this up automatically; the
capture never alters tool output and never blocks a session.

```python
engine.observe(tool="Bash", input_text="ls", output_text="README.md", session_id="s1")
hits = engine.recall_observations("README", session_id="s1")  # FTS5 snippet window
handoff = engine.compress_observations(session_id="s1")       # session → handoff
```

Recall is session-scoped by default, so a session only ever mines its own trail unless
you widen `scope`.

```bash
conscio govern status    # ceiling, obs.db size, capture health, baseline
conscio govern prefix    # measure your stable prefix and where compactions land
conscio govern on        # freeze a baseline + apply the cost-optimal window here
conscio govern report    # current vs baseline, from the host's own usage records
conscio govern off       # restore what you had before
```

`govern report` prices both sides **per turn** rather than as totals — a total against a
total mostly measures which side ran longer — and prints `—` where the baseline froze no
figure to compare against, rather than a zero that would render as a 100% saving.

> Capture is complete, not truncated: a tool's whole input and output are stored (up to
> 1 MiB per field), so anything a tool reads or writes — including secrets — can land in
> `obs.db`. It is a second copy of what the host already keeps in its session
> transcripts, with a 30-day retention window. Treat it as one more place to scrub, and
> lower `max_age_days` if that matters to you.

### Embodiment — the MCP server

`conscio-mcp` is a hand-rolled, **stdlib-only** MCP stdio server (newline-delimited
JSON-RPC 2.0), so any MCP host can plug into a Conscio instance and consume its
cognition live. Zero new dependency; nothing opens a socket.

The tool surface is sized to the model. Three nested surfaces, so raising one never
removes a tool:

| Surface | Tools served | Advertised schema |
|---|---|---|
| `lite` | 10 | ~570 tokens — descriptions flattened to ≤120 chars |
| `balanced` | 18 | ~1520 tokens |
| `ultra` (default) | 35 | ~3100 tokens |

Precedence is `--mode` on the CLI, then the persisted choice, then the default.
`conscio_mode` switches at runtime and is present in every surface — in `lite` it is the
only way back out. An unadvertised tool stays callable through `tools/call`, and tools
enabled by flag (act, review, relay) are never filtered — 42 with every flag on.

The base surface is **propose-only** (perceive / reflect / recall / audit); opt-in
`--enable-act` adds host-executed, ledgered, gated `act` — Conscio signs and audits the
intent, the host pulls the trigger. See [the MCP guide](docs/guides/mcp.md).

### Live mode — daemon, sensors & Awake Mode

Conscio can run as a **living process** that perceives the world each cycle and acts
**only when explicitly awake** (R9, default OFF):

```python
from conscio import ConsciousnessEngine, HostSensor
from conscio.daemon import Daemon

engine = ConsciousnessEngine("glm-5.1", storage_path="~/.conscio/live")
engine.wake()                                              # opt in to autonomy (persisted)
Daemon(engine, sensors=[HostSensor()], interval=30).run()  # perceive → reflect → act
```

`conscio-daemon --sensors host --interval 30` runs it standalone (add `--awake`);
`conscio awake` reaches an already-running daemon rather than waking a second engine of
its own. Reference sensors `HostSensor` / `AgentSensor` ship as `conscio.sensors` entry
points; write your own `SensorAdapter`.

### Society — shared minds

Same-host instances can **share locally-proven skills as data** through a host-shared
`noosphere.db` (publish → static-revalidated quarantine → sandboxed trial → promotion),
**audit each other's** action records, and exchange messages over the Liaison mailbox.
Engine-free, read-only on the live `conscio.db`, no inherited trust, no network.

### Intercepter

A model asked for `0.15 * 8000` will happily invent a number. Intercepter takes the
`[INTERCEPT: ...]` expressions it emits and *evaluates* them — a restricted AST walker
over arithmetic, comparisons, a fixed set of math functions and bound variables. No
`eval`, no `exec`, no attribute access, no imports; expressions are length- and
depth-capped. LaTeX is converted before parsing, and an equation with no bound variables
is solved rather than evaluated.

```python
from conscio.agency.intercepter import Intercepter

itc = Intercepter()
itc.set_variable("rate", 0.15)
itc.process("[INTERCEPT: rate * 8000]").text
# '[INTERCEPT: rate * 8000] -> [RESULT: 1200.0]'
```

`attach_adapter(..., intercept_enabled=True)` wires it into the act pipeline as an
`InterceptionLoop`: the model emits a tag, gets the real answer back, and may revise —
up to 3 iterations, each one an EventBus record.

---

## Architecture

```
            reflect  ── passive · advisory · append-only ──┐
                                                              │
  ConsciousnessEngine  (orchestrator · lifecycle · injection) │
   ├─ Witness        InnerMonologue · WorldModel · MetaCognition · GoalGenerator
   ├─ Substrate      ContentStore (FTS5 BM25 + RRF) · VectorBackend (HNSW/sqlite-vec/numpy) ·
   │                 HybridRetriever · EventBus · FilterPipeline
   ├─ Continuity     SessionLifecycle (6-step handoff) · SessionRAG (optional)
   ├─ Metabolism     MetabolicContext · DreamCycle (release→prune→…→distill)
   ├─ Coherence      CoherenceEngine · semantic reconciliation · unmeasured dimensions named
   ├─ Structural     StructuralDistiller (graph → ranked signal; data, not code)
   ├─ Evaluation     evaluate — 5/6-axis rubric
   ├─ Gates          decide · council · loop_gate · delivery_check · investigate
   ├─ Pipelines      acceptance_criteria · verify · continuous_loop ·
   │                 strategic_compact · ledger
   ├─ Diagnostics    context_budget · eval_harness · rules_distill
   ├─ Harness        PromptZones · CheckpointChain · TokenAccount+CPM ·
   │                 FailureGovernor · adaptive max_retries · skeptic skip
   ├─ Adaptive       prompt_complexity (full/compact/minimal) ·
   │                 auto-detect (--model auto) · FallbackAdapter
   ├─ Memory         KnowledgeGraph · Hallways · WingManager · Deduplicator ·
   │                 EntityDetector · EmbeddingProvider · Miner · Migration
   ├─ Observations   obs.db (FTS5, separate) · capture hooks ·
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
from a model is its **context window**: that single number selects the injection mode,
and nothing else is hardcoded to a particular model. A known model resolves offline and
deterministically; an unknown one is inferred from its name or pinned explicitly:

```python
from conscio import ModelRegistry
ModelRegistry.register("my-model", context_window=200_000)
```

**Adaptive prompt complexity.** `ProbeSuite` measures each model's `json_fidelity`,
`schema_depth` and `instruction_depth` (5 empirical probes, cached in SQLite), and
`prompt_complexity` picks a prompt tier from the profile:

| Tier | Persona | State | Memories | Few-shot | When |
|---|---|---|---|---|---|
| `full` | complete | ✓ | ✓ | ✓ | json_fidelity ≥ 0.8 + instruction_depth ≥ 2 |
| `compact` | 1-line | ✓ | ✗ | ✗ | instruction_depth ≥ 2 + schema_depth ≥ 2 |
| `minimal` | none | ✓ | ✗ | ✗ | otherwise (tiny models) |

**Auto-detect + fallback chain.** `--model auto` makes the MCP config fixed forever — on
boot Conscio calls `GET /v1/models`, filters out embedding models, tests each chat model
with a minimal prompt, and uses the first that responds. The winner is persisted, so the
next boot starts instantly. At runtime `FallbackAdapter` switches to the next model in
the chain on a permanent error, timeout or bad response.

### Benchmark (local LM Studio, 5 cycles)

| Model | json_fidelity | Tier | JSON valid | Tokens | Latency p50 | Catch rate |
|-------|--------------|------|------------|--------|-------------|------------|
| Qwen 0.8B | 1.0 | T2 | **100%** | 5357 | 19.0s | 100% |
| LFM 1.2B | 1.0 | T2 | **100%** | 4950 | 23.6s | 100% |

Both small models hit 100% JSON validity through Conscio (raw: 60% and 80%). Token cost
is 6–10× without prompt caching; with provider caching the stable zone caches at ~0.1×,
bringing the effective cost to ~2–3×.

---

## Vector backends

Three backends, auto-detected at startup — no config needed. Priority is
**HNSW → sqlite-vec → numpy**; override with `CONSCIO_VEC_BACKEND`.

Measured on 37,042 real embeddings, 384-dim, all-MiniLM-L6-v2:

| Backend | Search | Recall@10 | Ingest | Disk | RAM | Setup |
|---|---|---|---|---|---|---|
| **HNSW** | 2.9ms | 99% | 28s | 65MB | 300MB | `pip install hnswlib` |
| **sqlite-vec** | 17ms | 100% | 12s | 58MB | 0 | `pip install sqlite-vec` |
| **numpy** (default) | 180ms | 100% | 0 | 74MB | 0 | zero deps |

HNSW is 62× faster than numpy and 6× faster than sqlite-vec.

```bash
conscio migrate-vectors                  # → sqlite-vec (10×, default target)
conscio migrate-vectors --target hnsw    # → HNSW (50×, direct)
```

Every path auto-detects the source format, backs up first, verifies search rankings, and
writes the target. HNSW writes to a separate `hnsw.db`, so the original `vectors.db` is
never clobbered. Full guide: [docs/MIGRATION.md](docs/MIGRATION.md).

---

## Bench

```bash
conscio-bench --adapter mock                          # offline, deterministic
conscio-bench --adapter ollama:qwen3.5:0.8b --cycles 20
conscio-bench --adapter mock --skills 20              # skill-acquisition curve
```

Backends: `mock`, `ollama:<model>`, `llamacpp[:<name>]`, `lmstudio:<model>[@<base_url>]`,
`openai:<model>[@<base_url>]`. Reports probe profile, decode tier, per-tier syntactic
validity, Skeptic catch-rate, latency p50, and calibration. Baselines in `docs/bench/`.

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
# House rule: one file per pytest process (the full run OOMs on small machines; CI matches)
for f in tests/test_*.py; do pytest "$f" -q; done
pytest tests/test_agency_act.py -v    # a specific module
```

SQLite in WAL mode. The engine's storage defaults to `~/.hermes/consciousness/`, where
`conscio.db` holds EventBus + ActionLedger + skills, `content_store.db` holds the
ContentStore, and `obs.db` holds tool observations in a store of its own. Vector backends
write to `vectors.db` (sqlite-vec/numpy) and `hnsw.db`. Pass `storage_path=` (or
`--storage`) to move it; the CLI and daemon additionally honour `$HERMES_HOME`, which the
library default does not read. Cross-instance state — the knowledge graph, hallways,
vectors, dedup, handoffs, the act sandbox — lives under `~/.conscio/`. **Always** call
`engine.close()` or use the `with` statement so WAL checkpoints flush.

---

## License

AGPL-3.0-or-later — Neguiolidas / Neguitech
