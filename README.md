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

**Latest release — `v2.11.0` "Reach":** install Conscio **once** and bind **many
agentic hosts** (Claude Code, Antigravity, any MCP host) on one machine, each to its
own **space** (`~/.conscio/instances/<slug>/` — its own identity, memory, ledger)
while sharing one **society** (`~/.hermes/{noosphere,liaison}.db`). An interactive
**`conscio init`** wizard creates the space, emits + read-back-verifies the host's MCP
config, and for Claude Code materializes a native bundle: **10 `/conscio:*` slash
commands**, a `conscio` skill, and a defensive awareness hook. Debt-zero on the
cognitive core.

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
with ConsciousnessEngine(model_name="kimi-k2.6") as engine:
    result = engine.reflect(
        world_state="All systems operational",
        confidence=0.8,
        anomalies=["Unusual latency spike detected"],
    )
    injection = engine.get_state_for_injection()   # compact state for context injection
    engine.world.add_entity("server", "system", state="healthy")
    hits = engine.recall("latency incidents")       # cross-session memory (FTS5 + optional RAG)
```

`reflect()` is the **passive heart** and is never allowed to act. Everything that can
change the world lives behind `act()` and its safety gates — a separation that is
non-negotiable (see [Safety rules](#safety-rules-non-negotiable)).

---

## What Conscio does

- **Knows itself** — detects its model and context window (offline & deterministic by
  default; opt-in auto-detection), and adapts its footprint.
- **Reflects continuously** — a passive inner-monologue loop that observes, assesses
  confidence, and summarizes (`engine.reflect()` — advisory, never acts).
- **Generates its own goals** — driven by curiosity, maintenance, and evolution.
- **Acts under audit** — an opt-in agency layer (`engine.act()`) that proposes,
  audits, risk-gates, and only then executes — with a human gate for anything risky.
- **Learns procedures** — successful audited plans become reusable skills (procedural
  memory), fed back to the actor as few-shot exemplars.
- **Judges its own quality** — confidence calibration, blind-spot detection, and
  coherence/dissonance metrics; formal 5-axis self-evaluation (`evaluate()`).
- **Gates its own decisions** (v3.0) — ADRs (`decide`), multi-voice council
  (`council`), autonomous-loop gate (`loop_gate`), pre-close delivery check
  (`delivery_check`), and read-before-act verification (`investigate`).
- **Pipelines its own work** (v3.0) — intent-driven acceptance criteria, post-
  implementation verification, loop-pattern selection, strategic compaction
  advisory, and a recursive decision ledger with promotion gates.
- **Diagnoses its own context** (v3.0) — context-budget audit, eval harness with
  pass@k reliability metrics, and rule distillation from skills/events/decisions.
- **Stores & retrieves knowledge** — FTS5 BM25 dual-index with RRF merging; optional
  semantic recall.
- **Consolidates while idle** — a dream cycle that releases, prunes, reconciles,
  crystallizes, and distills.
- **Persists across sessions** — heartbeat/handoff continuity with on-demand injection.
- **Knows its codebase structurally** — optional, consent-gated ingestion of a
  Graphify graph, distilled to a compact signal injected budget-aware. Data, never
  code (R10).
- **Plugs into any host** — a stdlib-only MCP stdio server (`conscio-mcp`) feeds any
  CLI/IDE/agent its cognition and audited proposals live.

---

## Safety rules (non-negotiable)

1. **No autonomous self-modification** — evolution proposals require human approval.
2. **Context injection has hard limits** — never exceeds the mode budget.
3. **Goals never execute directly** — only through the audited `act()` pipeline
   (output contract + Skeptic audit + risk gating + earned autonomy + circuit breaker).
4. **Reflections are append-only** — never edited once written.
5. **Cannot modify its own safety rules** — no self-referential gate bypass.
6. **HIGH-risk actions always require human approval** — never auto-executed.
7. **No network in the tool registry** — the only network the core may touch is the
   InferenceAdapter (localhost by default).
8. **Every external effect goes through the ActionLedger** — append-only, auditable.
9. **Autonomous operation requires Awake Mode (R9)** — the self-initiated heartbeat
   only acts when the persisted `awake` flag is on; **default OFF**. Asleep, it
   perceives and `reflect()`s only. A human's direct `engine.act()` is not gated by R9.

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

engine.attach_adapter(OllamaAdapter(model="qwen3.5:0.8b"))   # or a frontier API
report = engine.act()                  # downstream of reflect(); proposes only (L1)
if report.status.value == "proposed":
    engine.approve(report.ledger_id)   # the human gate executes it

engine.probe()                         # lazy, empirical capability measurement
engine.run(budget=...)                 # L3 heartbeat: reflect → act → dream, gated
```

Autonomy is **earned and measured**, never assumed: `ProbeSuite` measures the attached
model, `TrustMatrix` grants L1/L2/L3 from real calibration and ledger history, and the
`CircuitBreaker` quarantines misbehaving goals. HIGH-risk actions are *always* queued
for a human (R6).

### Live mode — daemon, sensors & Awake Mode

Conscio can run as a **living process** that perceives the world each cycle and acts
**only when explicitly awake** (R9, default OFF):

```python
from conscio import ConsciousnessEngine, HostSensor
from conscio.daemon import Daemon

engine = ConsciousnessEngine("glm-5.1", storage_path="~/.conscio/live")
engine.wake()                                                 # opt in to autonomy (persisted)
Daemon(engine, sensors=[HostSensor()], interval=30).run()     # perceive → reflect → act
```

`conscio-daemon --sensors host --interval 30` runs it standalone (add `--awake` to
enable autonomy). Reference sensors `HostSensor` / `AgentSensor` ship as
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
pulls the trigger. See [the MCP guide](docs/guides/mcp.md).

### Society — shared minds

Same-host instances can **share locally-proven skills as data** through a host-shared
`noosphere.db` (publish → static-revalidated quarantine → sandboxed trial →
promotion), **audit each other's** action records, and exchange messages over the
Liaison mailbox (`hermes_review` cross-agent approvals + free-form relay). Engine-free,
read-only on the live `conscio.db`, no inherited trust, no network.

---

## Architecture

```
            reflect()  ── passive · advisory · append-only ──┐
                                                              │
  ConsciousnessEngine  (orchestrator · lifecycle · injection) │
   ├─ Witness        InnerMonologue · WorldModel · MetaCognition · GoalGenerator
   ├─ Substrate      ContentStore (FTS5 BM25 + RRF) · EventBus · FilterPipeline
   ├─ Continuity     SessionLifecycle (6-step handoff) · SessionRAG (optional)
   ├─ Metabolism     MetabolicContext · DreamCycle (release→prune→…→distill)
   ├─ Coherence      CoherenceEngine · semantic reconciliation
   ├─ Structural     StructuralDistiller (graph → ranked signal; data, not code)
   ├─ Gates (v3.0)   decide · council · loop_gate · delivery_check · investigate
   ├─ Pipelines (v3.0) acceptance_criteria · verify · continuous_loop ·
   │                 strategic_compact · ledger
   ├─ Diagnostics (v3.0) context_budget · eval_harness · rules_distill
   └─ Embodiment     conscio-mcp: JSON-RPC 2.0 over stdio (stdlib only)
                                                              │
            act()  ── opt-in agency · audited · gated ◀───────┘
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

## Bench

```bash
python -m conscio.bench --adapter mock                          # offline, deterministic
python -m conscio.bench --adapter ollama:qwen3.5:0.8b --cycles 20
python -m conscio.bench --adapter mock --skills 20              # skill-acquisition curve
```

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

SQLite in WAL mode, default `~/.conscio/data/` (`conscio.db` holds ContentStore +
EventBus + ActionLedger + skills). **Always** call `engine.close()` or use the `with`
statement so WAL checkpoints flush. Session continuity writes a compact heartbeat
(`<1.5KB`, auto-injected next session) plus a richer handoff and dated archives.

---

## License

AGPL-3.0-or-later — Neguiolidas / Neguitech
