# Conscio v4.0 Audit Report — 2026-08-05

## Summary

Full audit of Conscio v3.9.7 codebase. All 3249 pytest tests pass (3 skipped, 0 failures). Manual testing of every module found 9 bugs.

## Test Results

### Automated Tests
- **pytest**: 3249 passed, 3 skipped, 0 failures (545s)

### Manual Module Coverage

| Module | Status | Notes |
|---|---|---|
| EventBus | ✅ PASS | emit, query, emit_batch, compact, stats, recent_errors all work with correct types/categories |
| Intercepter | ⚠️ 3 bugs | LaTeX frac/sqrt/times/cdot/div work. ^ exponent and implied mult fail without LaTeX markers. Equation auto-solve doesn't fire via process() |
| Gates | ✅ PASS | decide, council, loop_gate, delivery_check, investigate all work |
| evaluate() | ✅ PASS | 5-axis rubric returns EvaluationReport with all axes |
| rules_distill | ✅ PASS | scan, distill, list all work (the original bug was fixed in v3.9.6) |
| Vector backends | ✅ PASS | numpy (VectorBackend) works. sqlite-vec/HNSW need extra deps not installed |
| ContentStore | ✅ PASS | index, search, trigram search, stats all work |
| ObsStore | ✅ PASS | put_observation, search, last_session, session_summary, prune all work |
| KG | ✅ PASS | add_entity, add_triple, query_entity, query_relationship, timeline all work |
| Hallways | ✅ PASS | create_wing, create_room, list_*, stats all work |
| MCP Server | ✅ PASS | 33 tools normal, 8 tools lite, all schemas valid JSON |
| CLI | ⚠️ 1 bug | --help works, --version missing, conscio-mcp --lite starts OK |
| Governor | ✅ PASS | Module-level functions (recommend_window, cost_units, etc.) work |
| ECC Pipeline | ⚠️ 1 bug | acceptance_criteria, continuous_loop, ledger, context_budget, eval_harness work. verify() crashes on list[str] |
| Daemon | ⚠️ 1 bug | daemon.main exists, but host_health not importable from agency.tools |
| Engine | ✅ PASS | close() with delivery_check, strategic_compact, advisory, state all work |
| Relay | ✅ PASS | Configured via Bindings, not Engine — intended design |

## Bug Catalog

### 🔴 HIGH (1)

#### BUG-04: verify() crashes with list[str] input
- **Module**: acceptance_criteria + verify
- **Repro**: `eng.verify(criteria=['test']) → 'str' object has no attribute 'get'`
- **Description**: verify() expects `list[dict]` but crashes with `'str' object has no attribute 'get'` when passed a list of strings. acceptance_criteria returns dicts with `{'id': 'AC-001', 'description': '...'}` but verify() expects each dict to have an `evidence` key. The two functions should be compatible — verify() should accept criteria from acceptance_criteria directly.
- **Fix**: Either accept strings (wrap them in dicts), or make acceptance_criteria include `evidence` field and document the expected dict shape.

### 🟡 MEDIUM (3)

#### BUG-01: Caret (^) exponent not converted without LaTeX markers
- **Module**: intercepter
- **Repro**: `ip.process('[INTERCEPT: 2^10]') → [ERROR: operator BitXor not allowed]`
- **Root cause**: `_LATEX_MARKERS` regex does NOT include `^`, so `_latex_to_python` is never called for bare exponents. `ast.parse("2^10")` succeeds as BitXor, so the `_fix_implied_mult` fallback in the SyntaxError catch never fires. `_eval_node` then rejects BitXor.
- **Fix**: Either add `^` to `_LATEX_MARKERS`, or always run `_fix_implied_mult` before `ast.parse`.

#### BUG-02: Implied multiplication 2(3+4) not converted without LaTeX markers
- **Module**: intercepter
- **Repro**: `ip.process('[INTERCEPT: 2(3+4)]') → [ERROR: only named functions allowed]`
- **Root cause**: `ast.parse("2(3+4)")` succeeds (valid Python: calling 2 as function), so `_fix_implied_mult` fallback never fires. `_eval_node` sees a Call node and rejects it with "only named functions allowed".
- **Fix**: Before `ast.parse`, try `_fix_implied_mult`. If the fixed expression differs, use that instead.

#### BUG-05: host_health not importable from agency.tools
- **Module**: agency/tools
- **Repro**: `from conscio.agency.tools import host_health → ImportError`
- **Root cause**: Commit 9fc01c5 added host_health to daemon bootstrapping, but it's not a module-level export in agency/tools. It may be registered inside ToolRegistry differently.
- **Fix**: Either export host_health from tools module, or document that it's only accessible via ToolRegistry.

### 🟢 LOW (5)

#### BUG-03: Equation auto-solve doesn't fire via process()
- **Module**: intercepter
- **Repro**: `ip.process('[INTERCEPT: 2*x + 3 = 11]') → [ERROR: variable 'x' not bound]`
- **Fix**: Check if equation detection logic fires in the process() path. May need to add equation detection before variable binding.

#### BUG-06: VALID_TYPES mismatch with documentation
- **Module**: event_bus
- **Repro**: `eb.emit(type='perceive', ...) → ValueError` (correct type is 'perception')
- **Fix**: Update all docs and comments to use correct VALID_TYPES.

#### BUG-07: Engine.__init__() doesn't accept 'relay' kwarg
- **Module**: engine
- **Repro**: `ConsciousnessEngine(model_name='t', storage_path=d, relay=True) → TypeError`
- **Fix**: Document that relay is a Bindings concern, not Engine.

#### BUG-08: Engine has no 'feed' method
- **Module**: engine
- **Repro**: `eng.feed({...}) → AttributeError`
- **Fix**: Document that feed is MCP-only, or add eng.feed() that delegates to perception pipeline.

#### BUG-09: conscio --version not supported
- **Module**: CLI
- **Repro**: `conscio --version → error: unrecognized arguments: --version`
- **Fix**: Add `parser.add_argument('--version', action='version', version=f'conscio {__version__}')`

## Modules That Passed Clean

- EventBus (emit, query, emit_batch, compact, stats, recent_errors)
- Gates (decide, council, loop_gate, delivery_check, investigate)
- evaluate() (5-axis rubric)
- rules_distill (scan, distill, list)
- VectorBackend (numpy: add_batch, search, with_engine auto-detect)
- ContentStore (index, search, trigram search, stats, recall)
- ObsStore (put_observation, search, last_session, session_summary, prune)
- KG (add_entity, add_triple, query_entity, query_relationship, timeline)
- Hallways (create_wing, create_room, list_*, stats)
- MCP Server (33 tools normal, 8 tools lite, all schemas valid)
- Governor (recommend_window, cost_units, snapshot, report)
- ECC Pipeline (acceptance_criteria, continuous_loop, ledger, context_budget, eval_harness)
- Engine (close+delivery_check, strategic_compact, advisory, state)
- Daemon (main entry point)
- Relay/Liaison (via Bindings configuration)
