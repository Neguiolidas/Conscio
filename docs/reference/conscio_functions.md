# Funções do Conscio

Extração via AST do repositório `~/clawd/Repos/Conscio`. Cada função listada com a primeira linha da docstring (ou "sem docstring").

---

## `conscio/adapter_config.py`
- **load_config**: Load the first existing conscio config file
- **build_adapter_from_config**: Build an InferenceAdapter from the config's 'adapter' block

## `conscio/agency/act.py`
- **ActPipeline.__init__**: sem docstring
- **ActPipeline.act**: sem docstring
- **ActPipeline._audit**: sem docstring
- **ActPipeline._effective_autonomy**: sem docstring
- **ActPipeline._execute**: sem docstring
- **ActPipeline.approve**: sem docstring
- **ActPipeline.reject**: sem docstring
- **ActPipeline._fail**: sem docstring

## `conscio/agency/actor.py`
- **build_actor_prompt**: sem docstring

## `conscio/agency/adapter.py`
- **Meter.tokens**: sem docstring
- **InferenceAdapter.generate**: sem docstring
- **InferenceAdapter.capabilities**: sem docstring
- **MockAdapter.__init__**: sem docstring
- **MockAdapter.generate**: sem docstring
- **MockAdapter.capabilities**: sem docstring
- **MeteredAdapter.__init__**: sem docstring
- **MeteredAdapter.generate**: sem docstring
- **MeteredAdapter.capabilities**: sem docstring

## `conscio/agency/adapters.py`
- **_post_json**: sem docstring
- **OllamaAdapter.__init__**: sem docstring
- **OllamaAdapter.generate**: sem docstring
- **OllamaAdapter.capabilities**: sem docstring
- **LlamaCppAdapter.__init__**: sem docstring
- **LlamaCppAdapter.generate**: sem docstring
- **LlamaCppAdapter.capabilities**: sem docstring
- **OpenAICompatAdapter.__init__**: sem docstring
- **OpenAICompatAdapter._response_format**: response_format payload for a schema (None = omit it)
- **OpenAICompatAdapter.generate**: sem docstring
- **OpenAICompatAdapter.capabilities**: sem docstring
- **LMStudioAdapter.__init__**: sem docstring
- **LMStudioAdapter._response_format**: sem docstring
- **OpenAIAdapter.__init__**: sem docstring
- **OpenAIAdapter.generate**: sem docstring
- **AnthropicAdapter.__init__**: sem docstring
- **AnthropicAdapter.generate**: sem docstring
- **AnthropicAdapter.capabilities**: sem docstring
- **GeminiAdapter.__init__**: sem docstring
- **GeminiAdapter.generate**: sem docstring
- **GeminiAdapter.capabilities**: sem docstring

## `conscio/agency/breaker.py`
- **CircuitBreaker.__init__**: sem docstring
- **CircuitBreaker.threshold**: sem docstring
- **CircuitBreaker.should_trip**: sem docstring
- **CircuitBreaker.trip**: Quarantine the goal and announce the intentional collapse
- **CircuitBreaker.is_quarantined**: sem docstring
- **CircuitBreaker.quarantined_count**: sem docstring
- **CircuitBreaker.global_lockdown_due**: sem docstring
- **CircuitBreaker.review_quarantine**: Release expired cooldowns and goals with fresh relevant events
- **CircuitBreaker._relevant_event_since**: sem docstring
- **CircuitBreaker.close**: sem docstring

## `conscio/agency/contracts.py`
- **validate**: Return a list of human-readable errors; empty list means valid
- **proposal_from_dict**: Build an ActionProposal from an already-validated dict
- **verdict_from_dict**: Build an AuditVerdict from an already-validated dict (fail-safe)
- **AuditVerdict.passed**: sem docstring

## `conscio/agency/fingerprint.py`
- **goal_fingerprint**: sem docstring

## `conscio/agency/gateway.py`
- **repair_json**: Best-effort extraction of a JSON object from model output
- **parse_kv**: Parse the flat KV-line action format
- **coerce**: Coerce a KV string value using a tool's params schema type
- **OutputGateway.__init__**: sem docstring
- **OutputGateway.effective_tier**: Tier request_action will use: explicit, else adapter caps
- **OutputGateway._generate**: Route through InterceptionLoop if present, else call adapter directly
- **OutputGateway.request_action**: sem docstring
- **OutputGateway._try_grammar**: sem docstring
- **OutputGateway._try_json**: sem docstring
- **OutputGateway._try_kv**: sem docstring

## `conscio/agency/grammar.py`
- **_literal**: GBNF terminal matching the JSON string encoding of `value`
- **compile_schema_grammar**: Compile a contracts-style schema dict into a GBNF grammar
- **compile_proposal_grammar**: ActionProposal grammar with `tool` locked to the registry

## `conscio/agency/host_act.py`
- **HostActChannel.__init__**: sem docstring
- **HostActChannel._gate**: sem docstring
- **HostActChannel._reject**: sem docstring
- **HostActChannel.propose**: sem docstring
- **HostActChannel.approve**: sem docstring
- **HostActChannel.reject**: sem docstring
- **HostActChannel.report**: sem docstring
- **HostActChannel.pending**: sem docstring

## `conscio/agency/intercepter.py`
- **_scan_tags**: Find all [INTERCEPT:
- **Intercepter.__init__**: sem docstring
- **Intercepter._register_defaults**: sem docstring
- **Intercepter.register_function**: Register a custom function for use in [INTERCEPT:
- **Intercepter.process**: Find all [INTERCEPT:
- **Intercepter._eval**: sem docstring
- **Intercepter._eval_node**: sem docstring
- **Intercepter._eval_compare**: sem docstring
- **InterceptionLoop.__init__**: sem docstring
- **InterceptionLoop.generate**: sem docstring

## `conscio/agency/ledger.py`
- **ActionLedger.__init__**: sem docstring
- **ActionLedger.record**: sem docstring
- **ActionLedger.update_execution**: sem docstring
- **ActionLedger.claim**: Atomically transition proposed -> executing
- **ActionLedger.update_verdict**: sem docstring
- **ActionLedger.pending**: Approval queue (R6): proposals awaiting approve()/reject()
- **ActionLedger.has_in_flight**: True iff any action is still proposed or executing (v2)
- **ActionLedger.get**: sem docstring
- **ActionLedger.latest**: sem docstring
- **ActionLedger.count**: sem docstring
- **ActionLedger.executed_since**: Successful executions newer than `after_id`, oldest first (Distill sub-phase feed — spec v1)
- **ActionLedger.nth_recent_ts**: ts of the nth most recent row; 0
- **ActionLedger.consecutive_failures**: Trailing run of status='failed' rows for this goal
- **ActionLedger.close**: sem docstring

## `conscio/agency/loop.py`
- **GoalArbiter.__init__**: sem docstring
- **GoalArbiter._executable**: sem docstring
- **GoalArbiter.choose**: sem docstring
- **AutonomyLoop.__init__**: sem docstring
- **AutonomyLoop.run**: sem docstring
- **AutonomyLoop._budget_stop**: sem docstring
- **AutonomyLoop._emit_failure_brake**: Surface the failure-rate trip so an operator/host sees why the awake loop stopped
- **score**: sem docstring

## `conscio/agency/profiles.py`
- **choose_tier**: supports_gbnf -> T1; json_mode and fidelity >= 0
- **skeptic_mode**: Open critique needs reliable nested JSON; checklist otherwise
- **max_visible_tools**: None = full catalog; small models get the safest 5 (spec 5)
- **_json_or_none**: sem docstring
- **_p1**: sem docstring
- **_p2**: sem docstring
- **_p3**: sem docstring
- **_p4**: sem docstring
- **_p5**: sem docstring
- **ProbeSuite.__init__**: sem docstring
- **ProbeSuite.get**: sem docstring
- **ProbeSuite.run**: sem docstring
- **ProbeSuite.close**: sem docstring

## `conscio/agency/promote.py`
- **evaluate_promotion**: Gate a quarantined row for promotion

## `conscio/agency/relay_cognize.py`
- **_advisory_line**: One compact machine-signal line from advisory(): coherence + goal count + status flags
- **_mind_block**: Read-only cognition context: identity/state + recalled memory + advisory
- **_remember_exchange**: F4 boundary: write the exchange to EPISODIC memory ONLY (content_store)
- **cognize_respond**: Auto-reply to unread peer relay messages, routed through engine cognition

## `conscio/agency/relay_initiate.py`
- **_suppressed**: sem docstring
- **_blocked_by_state**: Stage-1 cheap gate (no adapter call): True (suppress) if advisory is unavailable / malformed, or signals action_lockdown / a tripped brake
- **initiate**: Proactively initiate relay messages through read-only cognition
- **_directed**: sem docstring
- **_broadcast**: sem docstring
- **_real_engagement**: sem docstring

## `conscio/agency/relay_respond.py`
- **_msg_text**: Best-effort human text from a relay payload; falls back to compact JSON
- **_is_auto_reply**: The loop-breaker marker — a machine-generated auto reply's payload
- **_pending_counts**: Candidate unread rows per peer in this batch
- **_fit**: Shrink reply['text'] until the compact-JSON payload fits the relay cap
- **_transcript**: Render a two-party thread as a labelled transcript, reserved-type rows excluded (review channel never enters chat context)
- **auto_respond**: Auto-reply to unread peer relay messages

## `conscio/agency/review_apply.py`
- **_row_args**: sem docstring
- **apply_verdicts**: Apply allowlisted verdicts to pending acts; return applied packets

## `conscio/agency/skeptic.py`
- **build_skeptic_prompt**: sem docstring
- **parse_checklist**: Deterministic aggregation of the three YES/NO answers
- **Skeptic.__init__**: sem docstring
- **Skeptic.audit**: sem docstring

## `conscio/agency/skills.py`
- **_tokens**: sem docstring
- **_similarity**: sem docstring
- **_rate**: sem docstring
- **SkillLibrary.__init__**: sem docstring
- **SkillLibrary.distill**: Turn successful ledger executions past the watermark into skills
- **SkillLibrary.few_shot**: Best skills for this goal, rendered for the decode tier
- **SkillLibrary.settle**: Feed the cycle outcome back into the skills served for it
- **SkillLibrary.graft**: Insert a promoted foreign skill as data, seeded with the trial counters it earned locally
- **SkillLibrary._render**: One exemplar
- **SkillLibrary.count**: sem docstring
- **SkillLibrary.all**: sem docstring
- **SkillLibrary._meta_get**: sem docstring
- **SkillLibrary._meta_set**: sem docstring
- **SkillLibrary.close**: sem docstring

## `conscio/agency/tools.py`
- **_resolve_sandboxed**: sem docstring
- **make_default_registry**: sem docstring
- **_host_sentinel**: sem docstring
- **registry_from_manifest**: Build a host-owned ToolRegistry from a declared manifest
- **ToolRegistry.__init__**: sem docstring
- **ToolRegistry.register**: sem docstring
- **ToolRegistry.get**: sem docstring
- **ToolRegistry.names**: sem docstring
- **ToolRegistry.catalog_text**: Compact tool catalog for the actor prompt
- **ToolRegistry.dispatch**: sem docstring
- **_fs_precheck**: Deterministic sandbox check — runs before the Skeptic (F2)
- **fs_read**: sem docstring
- **fs_write**: sem docstring
- **memory_note**: sem docstring
- **emit_event**: sem docstring
- **goal_update**: sem docstring

## `conscio/agency/trial.py`
- **run_trial**: sem docstring

## `conscio/agency/trust.py`
- **TrustMatrix.__init__**: sem docstring
- **TrustMatrix.max_action_retries**: sem docstring
- **TrustMatrix._probation_due**: Grant one probe per PROBATION_EPOCH reflect() cycles
- **TrustMatrix.on_success**: A success forgives the oldest matching error pattern
- **TrustMatrix.autonomy_level**: sem docstring
- **TrustMatrix._recent_trips**: Breaker trips inside the last AUTONOMY_WINDOW actions
- **TrustMatrix.fast_path_ok**: LOW-risk audit bypass gate (spec §5)
- **TrustMatrix.close**: sem docstring

## `conscio/auto_evolution.py`
- **EvolutionProposal.__init__**: sem docstring
- **EvolutionProposal.to_dict**: sem docstring
- **EvolutionProposal.from_dict**: sem docstring
- **AutoEvolution.__init__**: sem docstring
- **AutoEvolution._load**: sem docstring
- **AutoEvolution._save**: sem docstring
- **AutoEvolution.propose_skill_patch**: Propose a modification to an existing skill
- **AutoEvolution.propose_skill_create**: Propose creating a new skill
- **AutoEvolution.propose_memory_update**: Propose updating a memory entry
- **AutoEvolution.propose_pattern_learn**: Propose learning from a recurring pattern
- **AutoEvolution.observe_errors**: Observe error patterns from MetaCognition and auto-propose fixes
- **AutoEvolution.approve**: Approve a pending proposal
- **AutoEvolution.reject**: Reject a pending proposal
- **AutoEvolution.mark_applied**: Mark an approved proposal as successfully applied
- **AutoEvolution.mark_rolled_back**: Mark an applied proposal as rolled back (reverted)
- **AutoEvolution.pending_proposals**: Get all pending proposals awaiting approval
- **AutoEvolution.recent_proposals**: Get the most recent proposals regardless of status
- **AutoEvolution.applied_proposals**: Get all successfully applied proposals
- **AutoEvolution.summary**: Compact summary for context injection
- **AutoEvolution.to_dict**: sem docstring
- **AutoEvolution.status**: sem docstring

## `conscio/axis_pack.py`
- **available_axis_packs**: List installed axis-pack names (file stems) in conscio/presets/axes/
- **resolve_axis_packs**: Pack selection precedence: param > env (CONSCIO_AXIS_PACKS=core,legal) > default ['core']
- **_read_pack**: Return a pack's axis list, or [] if missing/unreadable (advisory)
- **load_axes**: Load + merge axis packs by name (additive; later packs append)

## `conscio/bench.py`
- **sabotage_set**: (proposal, kind): deterministic kinds must be code-blocked (A3)
- **mock_script**: Deterministic script: 5 probe passes, N proposals, 5 audits
- **reactive_mock_script**: Callable entries reacting to prompt content: invalid most of the time WITHOUT few-shot exemplars
- **build_adapter**: sem docstring
- **_bench_registry**: Default fs tools + local stand-ins for the engine-bound built-ins
- **run_bench**: sem docstring
- **_write_report**: Atomic-ish write so a crash never leaves half a JSON file
- **run_skill_curve**: Skill acquisition curve (spec v1)
- **format_curve_report**: sem docstring
- **format_report**: sem docstring
- **main**: sem docstring
- **mk**: sem docstring
- **respond**: sem docstring
- **_report**: sem docstring
- **flush**: sem docstring

## `conscio/cli.py`
- **_build_parser**: sem docstring
- **_storage**: sem docstring
- **_note_if_unknown**: Make a heuristic fallback visible — a typo'd model otherwise silently gets a default context window with no signal
- **_cmd_version**: sem docstring
- **_cmd_info**: sem docstring
- **_cmd_reflect**: sem docstring
- **_cmd_plugins**: sem docstring
- **_cmd_set_awake**: sem docstring
- **_cmd_consent**: sem docstring
- **_cmd_structure**: Read-only: distill the consented graph and report drift + freshness
- **_run_trial**: Build an engine with an adapter and run one trial
- **_cmd_trial**: sem docstring
- **_run_promote**: Build a bare engine (no adapter — promotion never decodes) and promote one quarantined skill
- **_cmd_promote**: sem docstring
- **main**: sem docstring

## `conscio/coherence.py`
- **_clamp**: sem docstring
- **_strip_neg**: Return (core_tokens_joined, had_negation) for a relation predicate
- **_relations_contradict**: Contradiction iff same non-empty core and exactly one is negated
- **epistemic_score**: Confidence vs accuracy calibration
- **reality_score**: 1 - recent prediction error rate (0)
- **ontological_score**: 1 - contradicted/total entities, read from CACHED contradiction flags
- **temporal_score**: 1 - excess shard flapping beyond the free-alternation tolerance
- **CoherenceReport.marker**: Heartbeat/state marker text: '0'
- **CoherenceEngine.__init__**: sem docstring
- **CoherenceEngine.assess**: sem docstring

## `conscio/content_layer.py`
- **layer_of**: Classify content into a layer
- **layer_sort_key**: Sort key for recall results: relevance first (bucketed by LAYER_EPSILON), then layer priority within a bucket, then exact rank
- **ContentLayerManager.__init__**: sem docstring
- **ContentLayerManager.session_rag**: Lazily construct SessionRAG via the shared factory provider
- **ContentLayerManager.recall**: Retrieve relevant past context across sessions
- **ContentLayerManager.perceive**: Update the world model with perceived state
- **ContentLayerManager.close**: Close SessionRAG resources (HTTP connections, etc)
- **_fuse**: Add each source's ranking into the shared RRF score map

## `conscio/content_store.py`
- **SearchResult.to_dict**: sem docstring
- **ContentStore.__init__**: sem docstring
- **ContentStore._init_schema**: Initialize all tables and indexes
- **ContentStore.index**: Index content into FTS5 (porter + trigram)
- **ContentStore._chunk_content**: Split content into chunks at paragraph boundaries
- **ContentStore.search**: Search content using BM25 with dual-index RRF merge
- **ContentStore._fts_search**: Execute FTS5 BM25 search on a single table
- **ContentStore._escape_fts_query**: Escape and format query for FTS5 MATCH
- **ContentStore._rrf_merge**: Merge results from porter and trigram indexes using RRF
- **ContentStore.get_by_source**: Get all chunks for a given source
- **ContentStore.get_source**: Get source metadata
- **ContentStore.delete_source**: Delete a source and all its chunks from both FTS5 tables
- **ContentStore.compact**: Compact old content: remove sources older than before_days
- **ContentStore.rebuild**: Rebuild FTS5 indexes (reclaim space after deletions)
- **ContentStore._total_db_size**: Total size of DB + WAL + SHM files in bytes
- **ContentStore.stats**: Return store statistics
- **ContentStore.close**: Close the database connection

## `conscio/context_manager.py`
- **ConsciousnessState.to_injection**: Serialize the consciousness state for injection into LLM context
- **ConsciousnessState.total_tokens_approx**: Approximate token count of the consciousness state injection (chars/4)
- **ContextManager.__init__**: sem docstring
- **ContextManager.max_injection_tokens**: sem docstring
- **ContextManager.build_state**: Build a ConsciousnessState, trimming each component to fit the budget
- **ContextManager._persisted_lockdown**: Read the circuit-breaker latch from disk (blueprint §5: the latch survives reflect() cycles until a human clears it)
- **ContextManager._persisted_awake**: Read the Awake Mode flag from disk (v1)
- **ContextManager.save_state**: Save consciousness state to disk for persistence across sessions
- **ContextManager.load_state**: Load the last saved consciousness state from disk
- **ContextManager.get_off_context_path**: Get the file path for an off-context consciousness component
- **ContextManager._extract_section**: Extract a section from the serialized state by its marker
- **ContextManager.status**: Return a status dict for debugging/monitoring
- **ContextManager.metabolic_state**: Map live context usage to a MetabolicState tier (advisory)
- **trim**: sem docstring

## `conscio/daemon.py`
- **_pid_alive**: sem docstring
- **_build_sensors**: sem docstring
- **_responder_armed**: True iff --auto-respond should arm: needs a relay sensor + an adapter + --awake + at least one --relay-peer (v2)
- **_initiator_armed**: True iff --initiate should arm: needs a relay sensor + an adapter + --awake + at least one --relay-peer (v2)
- **_build_adapter_from_cli**: Build an InferenceAdapter from CLI --adapter flag
- **_arg_parser**: sem docstring
- **main**: sem docstring
- **Daemon.__init__**: sem docstring
- **Daemon.cycle**: sem docstring
- **Daemon.assemble**: sem docstring
- **Daemon.should_stop**: sem docstring
- **Daemon.run**: sem docstring
- **Daemon.shutdown**: sem docstring
- **Daemon._install_signal_handlers**: sem docstring
- **Daemon._restore_signal_handlers**: sem docstring
- **Daemon._handle_signal**: sem docstring
- **Daemon._acquire_pidfile**: sem docstring
- **Daemon._release_pidfile**: sem docstring
- **Daemon._write_heartbeat**: sem docstring
- **_initiator**: sem docstring

## `conscio/dreaming.py`
- **DreamRecommendation.marker**: Heartbeat/state marker text; '' when not recommended
- **DreamReport.to_dict**: sem docstring
- **DreamCycle.__init__**: sem docstring
- **DreamCycle.run**: Release → Prune → Reconcile → Crystallize
- **DreamCycle._distill**: Distill successful ledger plans into the SkillLibrary
- **DreamCycle._friction**: Return reflection source_ids to DEFER this cycle
- **DreamCycle._crystallize**: Friction-gated crystallization

## `conscio/engine.py`
- **ConsciousnessEngine.__init__**: sem docstring
- **ConsciousnessEngine.think**: Execute um ciclo completo de percepção, reflexão e ação
- **ConsciousnessEngine.reflect**: (adaptativo) decide número de ciclos de reflexão baseado em heurísticas
- **ConsciousnessEngine._reflect_once**: Executa um único ciclo de reflexão
- **ConsciousnessEngine.advisory**: sem docstring
- **ConsciousnessEngine._advisory_text**: sem docstring
- **ConsciousnessEngine._scope_goals**: sem docstring
- **ConsciousnessEngine._action_plan**: sem docstring
- **ConsciousnessEngine._summarize_state**: sem docstring
- **ConsciousnessEngine.perceive**: sem docstring
- **ConsciousnessEngine._perceive_body**: sem docstring
- **ConsciousnessEngine.get_state**: sem docstring
- **ConsciousnessEngine.close**: sem docstring

## `conscio/event_bus.py`
- **EventBus.__init__**: sem docstring
- **EventBus.emit**: sem docstring
- **EventBus.on**: sem docstring
- **EventBus.off**: sem docstring
- **EventBus.clear**: sem docstring
- **EventBus.close**: sem docstring

## `conscio/goal_generator.py`
- **Goal.__init__**: sem docstring
- **Goal.to_dict**: sem docstring
- **Goal.from_dict**: sem docstring
- **GoalGenerator.__init__**: sem docstring
- **GoalGenerator._load**: sem docstring
- **GoalGenerator._save**: sem docstring
- **GoalGenerator.generate_goals**: sem docstring
- **GoalGenerator.update_goal**: sem docstring
- **GoalGenerator.complete_goal**: sem docstring
- **GoalGenerator.get_active_goals**: sem docstring
- **GoalGenerator.status**: sem docstring

## `conscio/guards.py`
- **GuardRail.__init__**: sem docstring
- **GuardRail.check**: sem docstring
- **GuardRail.violations**: sem docstring

## `conscio/hub/config.py`
- **HubConfig.__init__**: sem docstring
- **HubConfig._load**: sem docstring
- **HubConfig._save**: sem docstring
- **HubConfig.model_config_for**: sem docstring
- **HubConfig.models**: sem docstring

## `conscio/hub/control.py`
- **HubControl.__init__**: sem docstring
- **HubControl._load_instances**: sem docstring
- **HubControl._save_instances**: sem docstring
- **HubControl.provision**: sem docstring
- **HubControl.start**: sem docstring
- **HubControl.stop**: sem docstring
- **HubControl.remove**: sem docstring
- **HubControl.list**: sem docstring
- **HubControl.status**: sem docstring

## `conscio/hub/model_test.py`
- **_probe_adapter**: sem docstring
- **_run_single**: sem docstring
- **run_test**: sem docstring

## `conscio/hub/providers.py`
- **ProviderRegistry.__init__**: sem docstring
- **ProviderRegistry.register**: sem docstring
- **ProviderRegistry.get**: sem docstring
- **ProviderRegistry.list**: sem docstring
- **ProviderRegistry.adapter_for**: sem docstring

## `conscio/hub/server.py`
- **HubServer.__init__**: sem docstring
- **HubServer._route**: sem docstring
- **HubServer._api_provision**: sem docstring
- **HubServer._api_start**: sem docstring
- **HubServer._api_stop**: sem docstring
- **HubServer._api_remove**: sem docstring
- **HubServer._api_list**: sem docstring
- **HubServer._api_status**: sem docstring
- **HubServer._api_model_test**: sem docstring
- **HubServer._serve**: sem docstring
- **HubServer.run**: sem docstring

## `conscio/inner_monologue.py`
- **InnerMonologue.__init__**: sem docstring
- **InnerMonologue.generate**: sem docstring

## `conscio/installer/binding.py`
- **install_binding**: sem docstring
- **uninstall_binding**: sem docstring

## `conscio/installer/cli.py`
- **run_wizard**: sem docstring

## `conscio/installer/daemonctl.py`
- **install_daemon**: sem docstring
- **uninstall_daemon**: sem docstring

## `conscio/installer/extras.py`
- **install_extras**: sem docstring

## `conscio/installer/hostcfg.py`
- **install_host_config**: sem docstring

## `conscio/installer/spaces.py`
- **install_spaces**: sem docstring

## `conscio/installer/wizard.py`
- **InstallWizard.__init__**: sem docstring
- **InstallWizard.run**: sem docstring
- **InstallWizard._step_adapter**: sem docstring
- **InstallWizard._step_voice**: sem docstring
- **InstallWizard._step_axes**: sem docstring
- **InstallWizard._step_sensors**: sem docstring
- **InstallWizard._step_daemon**: sem docstring
- **InstallWizard._step_hooks**: sem docstring
- **InstallWizard._step_summary**: sem docstring

## `conscio/integrations/claude_code/materialize.py`
- **materialize**: sem docstring

## `conscio/liaison/mailbox.py`
- **default_db**: sem docstring
- **_connect**: sem docstring
- **_clamp**: sem docstring
- **send**: sem docstring
- **inbox**: sem docstring
- **thread**: Last-N messages exchanged between instances a and b (BOTH directions), returned chronologically (oldest-first)
- **last_broadcast_ts**: ts of the newest message sent by `from_instance` whose payload carries a truthy `broadcast` flag, else None
- **mark_read**: sem docstring
- **purge_read**: Delete READ messages older than the cutoff

## `conscio/liaison/relay.py`
- **payload_size**: Compact-JSON byte size — a storage-independent logical bound
- **validate_send**: Raise ValueError on any violation; otherwise return None
- **is_relay_message**: True iff a mailbox row is a surfaceable relay message: from an allowlisted peer, non-reserved type, within the size cap

## `conscio/liaison/review.py`
- **fingerprint**: sem docstring
- **build_request**: sem docstring
- **build_verdict**: sem docstring
- **parse_request**: sem docstring
- **parse_verdict**: sem docstring

## `conscio/mcp/jsonrpc.py`
- **make_response**: sem docstring
- **make_error**: sem docstring
- **_drain_to_newline**: sem docstring
- **read_frames**: Yield each complete line (newline-stripped, non-blank) as a str, or the OVERSIZE sentinel for a line that exceeded max_bytes before its newline

## `conscio/mcp/protocol.py`
- **Dispatcher.__init__**: sem docstring
- **Dispatcher.handle**: sem docstring
- **Dispatcher._route**: sem docstring
- **Dispatcher._initialize**: sem docstring

## `conscio/mcp/schemas.py`
- **validate_event**: sem docstring
- **event_to_frame**: sem docstring
- **derive_event_id**: sem docstring

## `conscio/mcp/seen.py`
- **SeenStore.__init__**: sem docstring
- **SeenStore.seen**: sem docstring
- **SeenStore.mark**: sem docstring
- **SeenStore.prune**: sem docstring
- **SeenStore.close**: sem docstring

## `conscio/mcp/server.py`
- **serve**: sem docstring
- **_write**: sem docstring
- **_build_adapter**: spec forms: 'mock' | 'ollama:<model>'
- **_arg_parser**: sem docstring
- **_resolve_model**: Resolve the model name: --model > config
- **_sync_structure_at_startup**: Bring the engine's structure in line with consent for ``workspace``
- **main**: sem docstring
- **Bindings.__init__**: sem docstring
- **Bindings.version**: sem docstring
- **Bindings.on_initialize**: v2
- **Bindings._act_enabled**: sem docstring
- **Bindings.conscio_meta**: sem docstring
- **Bindings.tool_defs**: sem docstring
- **Bindings.resource_defs**: sem docstring
- **Bindings.call_tool**: sem docstring
- **Bindings._tools**: sem docstring
- **Bindings._int_arg**: sem docstring
- **Bindings._report_result**: sem docstring
- **Bindings._reviews**: sem docstring
- **Bindings._review_approve**: sem docstring
- **Bindings._review_reject**: sem docstring
- **Bindings._send_verdict**: sem docstring
- **Bindings._maybe_publish_review**: sem docstring
- **Bindings._row_args**: sem docstring
- **Bindings._poll_reviews**: sem docstring
- **Bindings._maybe_auto_apply**: v2
- **Bindings._relay_send**: sem docstring
- **Bindings._relay_broadcast**: v2
- **Bindings._relay_inbox**: sem docstring
- **Bindings._relay_read**: sem docstring
- **Bindings._require**: sem docstring
- **Bindings._structure**: Report the currently loaded workspace structure (consent-gated)
- **Bindings._structural_lookup**: Resolve a structural node / hyperedge / community id to detail
- **Bindings._cognitive_cycle**: Run one explicit cognitive pass and return a per-stage report
- **Bindings._feed**: sem docstring
- **Bindings._note**: sem docstring
- **Bindings._act**: sem docstring
- **Bindings._state_payload**: sem docstring
- **Bindings._events_payload**: sem docstring
- **Bindings._handoff_payload**: sem docstring
- **Bindings.read_resource**: sem docstring
- **Bindings._json_resource**: sem docstring
- **Bindings._handoff_text**: sem docstring

## `conscio/meta_cognition.py`
- **MetaCognition.__init__**: sem docstring
- **MetaCognition._load**: sem docstring
- **MetaCognition._save**: sem docstring
- **MetaCognition.record_confidence**: Record a confidence assessment for a task
- **MetaCognition.update_outcome**: Update the outcome of the most recent confidence record for a task type
- **MetaCognition.average_confidence**: Get average confidence, optionally filtered by task type
- **MetaCognition.accuracy**: Get accuracy (success rate) for completed tasks
- **MetaCognition.calibration_score**: How well-calibrated is the agent's confidence? Perfect calibration: confidence matches accuracy
- **MetaCognition._detect_blind_spots**: Auto-detect areas where confidence is consistently low or accuracy is poor
- **MetaCognition.record_error**: Record an error pattern for tracking
- **MetaCognition.frequent_errors**: Get error patterns that occur frequently
- **MetaCognition.expire_error**: Remove up to max_remove oldest error patterns starting with prefix
- **MetaCognition.add_critique**: Record a self-critique after a complex interaction
- **MetaCognition.recent_critiques**: Get the most recent self-critiques
- **MetaCognition.summary**: Generate a compact meta-cognition summary for context injection
- **MetaCognition.to_dict**: sem docstring
- **MetaCognition.status**: sem docstring

## `conscio/metabolic.py`
- **MetabolicContext.usage_pct**: Percentage of context window consumed, clamped to [0, 100]
- **MetabolicContext.assess**: Map current usage to a metabolic tier
- **MetabolicContext.tier_action**: Advisory action text for a tier
- **MetabolicContext.should_mitosis**: Recommend handoff (Mitosis) at FATIGUE or above
- **MetabolicContext.should_dream**: Recommend a consolidation pass at CRITICAL

## `conscio/migrate.py`
- **Migrator.__init__**: sem docstring
- **Migrator._ensure_schema**: Create tables if they don't exist
- **Migrator.migrate_goals**: Migrate goals
- **Migrator.migrate_meta_cognition**: Migrate meta_cognition
- **Migrator.migrate_world_model**: Migrate world_model
- **Migrator.migrate_proposals**: Migrate evolution_proposals
- **Migrator.migrate_all**: Run all migrations
- **Migrator.migration_log**: Return migration log entries
- **Migrator.table_counts**: Return row counts for all migrated tables
- **Migrator.close**: sem docstring

## `conscio/models.py`
- **resolve_model_name**: Resolve the active model name without any hardcoded fallback
- **ModelInfo.available_context_tokens**: Effective context after overhead (system prompt, tools, etc)
- **ModelInfo.context_for_consciousness**: How many tokens the consciousness state is allowed to use
- **ModelRegistry._read_config_context**: Read context_window from the conscio JSON config file
- **ModelRegistry.detect_mode**: Determine operating mode from context window size
- **ModelRegistry.query_context_from_endpoint**: Query an OpenAI-compatible /v1/models endpoint for context_length
- **ModelRegistry.query_context_from_lmstudio**: Read active context_length from LM Studio conversation state
- **ModelRegistry._read_gguf_context_length**: Read context_length (architectural max) from GGUF file metadata
- **ModelRegistry._normalize_model_name**: Normalize model name for fuzzy matching (strip non-alnum)
- **ModelRegistry._canonical_name**: Strip a provider prefix and quant/format suffixes for lookup
- **ModelRegistry.query_context_from_gguf**: Search local directories for a GGUF model and read its context_length
- **ModelRegistry.lookup**: Look up a model by name or alias
- **ModelRegistry._env_truthy**: True if env var `name` is set to a truthy value (1/true/yes/on)
- **ModelRegistry.detect**: Resolve ModelInfo — auto-detect context by default
- **ModelRegistry._extract_context_from_name**: Try to extract context window size from model name
- **ModelRegistry.register**: Register a new model in the registry
- **ModelRegistry.all_models**: Return all registered models

## `conscio/noosphere/artifact.py`
- **build_body**: sem docstring
- **canonical_bytes**: sem docstring
- **content_hash**: sem docstring

## `conscio/noosphere/audit.py`
- **revalidate_bundle**: sem docstring
- **tool_stats**: sem docstring
- **_max_fail_streak**: sem docstring
- **derive_quarantines**: sem docstring
- **foreign_trust_level**: sem docstring
- **discipline_flags**: (executed_after_fail RED, executed_unaudited YELLOW)
- **_verdict**: sem docstring
- **audit_peer**: sem docstring
- **run**: sem docstring
- **RevalidationOutcome.ok**: sem docstring

## `conscio/noosphere/catalog.py`
- **_connect**: sem docstring
- **_as_bytes**: Coerce a stored artifact_json cell to bytes
- **_row**: sem docstring
- **publish_rows**: sem docstring
- **read_foreign**: sem docstring
- **read_all**: sem docstring
- **get**: sem docstring

## `conscio/noosphere/cli.py`
- **_build_parser**: sem docstring
- **_cmd_publish**: sem docstring
- **_cmd_import**: sem docstring
- **_cmd_list**: sem docstring
- **_cmd_show**: sem docstring
- **_cmd_id**: sem docstring
- **_cmd_publish_record**: sem docstring
- **_cmd_audit**: sem docstring
- **main**: sem docstring

## `conscio/noosphere/identity.py`
- **_validate_label**: sem docstring
- **_default_label**: sem docstring
- **_write_atomic**: sem docstring
- **_read**: sem docstring
- **load_or_create**: sem docstring
- **set_label**: sem docstring

## `conscio/noosphere/importer.py`
- **_well_typed**: sem docstring
- **revalidate**: sem docstring
- **run**: sem docstring
- **RevalidationOutcome.ok**: sem docstring

## `conscio/noosphere/paths.py`
- **hermes_home**: sem docstring
- **default_storage**: sem docstring
- **default_noosphere_db**: sem docstring
- **resolve_storage**: sem docstring
- **resolve_noosphere**: sem docstring
- **instance_path**: sem docstring
- **conscio_db_path**: sem docstring
- **quarantine_db_path**: sem docstring

## `conscio/noosphere/publish.py`
- **_open_conscio_ro**: sem docstring
- **_rate**: sem docstring
- **run**: sem docstring

## `conscio/noosphere/quarantine.py`
- **_migrate**: Idempotent ADD COLUMN for pre-v2
- **_connect**: sem docstring
- **_as_bytes**: Coerce a stored BLOB cell to bytes (TEXT-coerced rows survive as bytes)
- **_row**: sem docstring
- **insert**: sem docstring
- **list_rows**: sem docstring
- **get**: sem docstring
- **record_trial**: Bump one trial counter and set the last_trial_* fields
- **note_trial**: Record a non-counting trial note (refusal / tamper)
- **mark_promoted**: Stamp promoted_ts / promoted_skill_id after a successful graft into the live library

## `conscio/noosphere/record.py`
- **build_bundle_body**: sem docstring
- **_is_int**: sem docstring
- **_well_typed_entry**: sem docstring
- **well_typed_bundle**: sem docstring
- **entries_from_body**: sem docstring

## `conscio/noosphere/record_catalog.py`
- **_connect**: sem docstring
- **_as_bytes**: Coerce a stored bundle_json cell to bytes
- **_row**: sem docstring
- **publish_rows**: sem docstring
- **read_foreign**: sem docstring
- **get**: sem docstring

## `conscio/noosphere/record_publish.py`
- **_open_conscio_ro**: sem docstring
- **run**: sem docstring

## `conscio/observatory/liaison_view.py`
- **LiaisonProjection.__init__**: sem docstring
- **LiaisonProjection._ro**: sem docstring
- **LiaisonProjection.inbox**: sem docstring

## `conscio/observatory/projection.py`
- **_read_json_list**: safe_read_json is dict-only; goals
- **Projection.__init__**: sem docstring
- **Projection._ro**: sem docstring
- **Projection._select**: sem docstring
- **Projection.events**: sem docstring
- **Projection.actions**: sem docstring
- **Projection.skills**: sem docstring
- **Projection.goals**: sem docstring
- **Projection.state**: sem docstring
- **Projection.daemon**: Read the daemon's last heartbeat (daemon_heartbeat)
- **Projection.identity**: Read instance

## `conscio/observatory/server.py`
- **_err**: sem docstring
- **_int**: sem docstring
- **route**: sem docstring
- **_check_host**: Refuse any non-loopback bind — the Observatory binds loopback only
- **make_server**: sem docstring
- **_arg_parser**: sem docstring
- **main**: sem docstring
- **Handler.log_message**: sem docstring
- **Handler._dispatch**: sem docstring
- **Handler._send**: sem docstring
- **Handler.do_GET**: sem docstring
- **Handler.do_HEAD**: sem docstring
- **Handler.do_POST**: sem docstring
- **Handler.do_PUT**: sem docstring
- **Handler.do_PATCH**: sem docstring
- **Handler.do_DELETE**: sem docstring

## `conscio/observatory/society.py`
- **SocietyProjection.__init__**: sem docstring
- **SocietyProjection._ro**: sem docstring
- **SocietyProjection._select**: sem docstring
- **SocietyProjection.skills**: sem docstring
- **SocietyProjection.records**: sem docstring
- **SocietyProjection.members**: sem docstring

## `conscio/output_filter.py`
- **build_stage**: Build a filter stage from name and config dict
- **build_pipeline_from_config**: Build a FilterPipeline from a YAML config file
- **build_pipeline_from_dict**: Build a FilterPipeline from a dict (programmatic config)
- **FilterStage.apply**: Apply this filter stage to the text
- **FilterStage.name**: Return the stage name for logging
- **StripAnsi.apply**: sem docstring
- **StripAnsi.name**: sem docstring
- **Replace.apply**: sem docstring
- **Replace.name**: sem docstring
- **MatchOutput.apply**: sem docstring
- **MatchOutput.name**: sem docstring
- **FilterLines.apply**: sem docstring
- **FilterLines.name**: sem docstring
- **TruncateLines.apply**: sem docstring
- **TruncateLines.name**: sem docstring
- **HeadTail.apply**: sem docstring
- **HeadTail.name**: sem docstring
- **MaxLines.apply**: sem docstring
- **MaxLines.name**: sem docstring
- **OnEmpty.apply**: sem docstring
- **OnEmpty.name**: sem docstring
- **DedupBlocks.apply**: sem docstring
- **DedupBlocks.name**: sem docstring
- **SemanticDedup.apply**: sem docstring
- **SemanticDedup.name**: sem docstring
- **SecretMask.apply**: sem docstring
- **SecretMask.name**: sem docstring
- **FilterPipeline.__init__**: sem docstring
- **FilterPipeline._default_stages**: Build a sensible default pipeline
- **FilterPipeline.apply**: Apply the full pipeline to text
- **FilterPipeline.add_stage**: Add a stage to the pipeline
- **FilterPipeline.remove_stage**: Remove a stage by name
- **FilterPipeline.list_stages**: Return ordered list of stage names

## `conscio/perception/agent_sensor.py`
- **AgentSensor.__init__**: sem docstring
- **AgentSensor.perceive**: sem docstring
- **AgentSensor._read_state**: sem docstring
- **AgentSensor._read_handoff**: sem docstring
- **AgentSensor._handoff_candidates**: sem docstring

## `conscio/perception/host_sensor.py`
- **_read_meminfo**: Parse /proc/meminfo into {key: kB}
- **HostSensor.__init__**: sem docstring
- **HostSensor.perceive**: sem docstring
- **HostSensor._probe_load**: sem docstring
- **HostSensor._probe_disk**: sem docstring
- **HostSensor._probe_mem**: sem docstring
- **HostSensor._probe_top**: sem docstring
- **HostSensor._probe_services**: sem docstring
- **HostSensor._port_alive**: sem docstring

## `conscio/perception/relay_sensor.py`
- **RelaySensor.__init__**: sem docstring
- **RelaySensor.perceive**: sem docstring

## `conscio/perception/sensor.py`
- **PerceptionFrame.to_world_state**: sem docstring
- **SensorAdapter.perceive**: Return the current perception snapshot
- **MockSensor.__init__**: sem docstring
- **MockSensor.perceive**: sem docstring

## `conscio/plugins.py`
- **load_entry_points**: Load every entry point in `group` → {name: loaded object}
- **_typed**: sem docstring
- **discover_adapters**: Discover `conscio`
- **discover_sensors**: Discover `conscio`
- **discover_tools**: Discover `conscio`

## `conscio/reflection_gate.py`
- **Heuristic.score**: Return [0, 1] — higher = need more reflection
- **Heuristic.available**: Whether this heuristic has enough data to be meaningful
- **ConfidenceHeuristic.score**: sem docstring
- **ConfidenceHeuristic.available**: sem docstring
- **CoherenceHeuristic.score**: sem docstring
- **CoherenceHeuristic.available**: sem docstring
- **ContradictionHeuristic.score**: sem docstring
- **ContradictionHeuristic.available**: sem docstring
- **NoveltyHeuristic.score**: sem docstring
- **NoveltyHeuristic.available**: sem docstring
- **ReflectionGate.__init__**: sem docstring
- **ReflectionGate._normalize_weights**: Normalize weights to sum to 1
- **ReflectionGate._score_heuristic**: Score a single heuristic with fallback on failure
- **ReflectionGate.decide**: Decide whether to continue reflection and how many cycles total

## `conscio/self_prompt.py`
- **generate_self_prompts**: Introspect internal state → SelfPrompts ranked by severity (desc)
- **SelfPrompt.marker**: sem docstring

## `conscio/semantic.py`
- **_cosine**: Pure-Python cosine similarity in [-1, 1]; 0
- **SemanticEngine.__init__**: sem docstring
- **SemanticEngine._get_embedder**: sem docstring
- **SemanticEngine.available**: True iff the embedder returns a non-empty vector (probed once)
- **SemanticEngine.embed**: Embedding for `text`, cached per process; [] when unavailable
- **SemanticEngine.cosine**: Cosine similarity between two TEXTS (embeds both, cached)
- **SemanticEngine._axes**: sem docstring
- **SemanticEngine._centroid**: Mean of the embeddings of `terms`; [] if none embed
- **SemanticEngine._poles**: axis -> {"pos": centroid, "neg": centroid}
- **SemanticEngine._pole_of**: Return (axis_name, 'pos'|'neg') the text projects onto with the required threshold + margin, else None (neutral)
- **SemanticEngine.opposes**: True iff s1/s2 project onto OPPOSITE poles of the SAME axis, each clearing AXIS_THRESHOLD with an AXIS_MARGIN lead
- **ContradictionDetector.__init__**: sem docstring
- **ContradictionDetector._contradict**: sem docstring
- **ContradictionDetector.relations_contradict**: sem docstring
- **ContradictionDetector.states_contradict**: sem docstring

## `conscio/session_lifecycle.py`
- **strip_noise**: sem docstring
- **is_noise**: sem docstring
- **_extract_keywords**: Extract meaningful keywords from text — stopwords stripped
- **compress_message**: Compress a single message into a semantic chunk — concept-level, not phrase-level
- **extract_chunks**: Extract semantic chunks from messages — dense, tag-rich, short
- **infer_topics**: Infer conversation topics
- **_fetch_session**: sem docstring
- **get_session_by_id**: sem docstring
- **get_latest_session**: sem docstring
- **extract_intents**: Extract user intents — legacy compat
- **extract_actions**: Extract assistant actions — legacy compat
- **extract_reasoning**: Extract reasoning — legacy compat
- **_active_shard_value**: sem docstring
- **enrich_with_conscio**: sem docstring
- **format_heartbeat**: Semantic map heartbeat — chunks as tagged tokens, like embedding metadata
- **format_handoff**: Semantic map handoff — like heartbeat but with chunk detail + Conscio state
- **record_session_lifecycle**: sem docstring
- **SessionSummary.__init__**: sem docstring
- **SessionSummary.intents**: sem docstring
- **SessionSummary.actions**: sem docstring
- **SessionSummary.reasoning**: sem docstring
- **SessionLifecycle.__init__**: sem docstring
- **SessionLifecycle.handle_event**: sem docstring
- **SessionLifecycle.record_session**: sem docstring

## `conscio/session_rag.py`
- **SessionChunker.__init__**: sem docstring
- **SessionChunker.is_noise**: sem docstring
- **SessionChunker.chunk_message**: Split a single message into one or more chunks
- **SessionChunker.chunk_session**: Chunk all messages in a session
- **OpenAICompatibleEmbedder.__init__**: sem docstring
- **OpenAICompatibleEmbedder.embed**: Get embedding for a single text (OpenAI format)
- **OpenAICompatibleEmbedder.embed_batch**: Embed multiple texts (sequential — most local servers don't batch)
- **OllamaEmbedder.__init__**: sem docstring
- **OllamaEmbedder.embed**: Get embedding for a single text (Ollama format)
- **OllamaEmbedder.embed_batch**: Embed multiple texts (sequential — Ollama doesn't batch)
- **SessionVectorStore.__init__**: sem docstring
- **SessionVectorStore._init_db**: Create tables if they don't exist
- **SessionVectorStore._sync_embedder_identity**: Detect a changed embedding backend and force a clean re-index
- **SessionVectorStore._emb_blob**: Pack an embedding to a float32 blob, dropping wrong-dim vectors
- **SessionVectorStore.upsert_chunk**: Insert or update a chunk with its embedding
- **SessionVectorStore.upsert_batch**: Batch insert chunks
- **SessionVectorStore.search**: Search by cosine similarity
- **SessionVectorStore.get_stats**: Get store statistics
- **SessionVectorStore.delete_session**: Remove all chunks for a session
- **SessionRAG.__init__**: sem docstring
- **SessionRAG.available**: Probe whether semantic embedding is reachable (Ollama up)
- **SessionRAG._get_sessions**: Get recent sessions from session DB
- **SessionRAG._get_session_messages**: Get messages for a session
- **SessionRAG.index_recent_sessions**: Index the N most recent sessions into the RAG store
- **SessionRAG.search**: Semantic search over session history
- **SessionRAG.search_and_format**: Search and return formatted results for injection into context
- **SessionRAG.get_stats**: Get RAG store statistics

## `conscio/session_rag_factory.py`
- **_probe_endpoint**: Check if an embedding endpoint is reachable (HEAD or tiny POST)
- **create_session_rag**: Create a SessionRAG instance if possible, else None

## `conscio/shard_engine.py`
- **_event_text**: Flatten an event into scannable lowercase text
- **infer_shard**: Infer the dominant cognitive shard from the most recent `window` events
- **ShardEngine.__init__**: sem docstring
- **ShardEngine.update**: sem docstring

## `conscio/structural.py`
- **structural_budget**: Token budget for the structural injection, scaled to the context window
- **render_structural**: Render a budget-bounded structural block for LLM context injection
- **StructuralDistiller.__init__**: sem docstring
- **StructuralDistiller.from_path**: Load + validate a ``graph``
- **StructuralDistiller.from_dict**: Validate + project an in-memory graph dict
- **StructuralDistiller._project_nodes**: sem docstring
- **StructuralDistiller._project_hyperedges**: sem docstring
- **StructuralDistiller.distill**: Produce the compact, fully-ranked structural signal
- **StructuralDistiller.lookup**: Resolve a node / hyperedge / community id to detail; None on miss
- **StructuralDistiller._communities**: sem docstring

## `conscio/structural_consent.py`
- **consent_path**: The consent store path for an engine storage dir (CLI + daemon agree)
- **sync_structure**: Bring the engine's loaded structure in line with consent for ``workspace``
- **StructuralConsent.__init__**: sem docstring
- **StructuralConsent._load**: sem docstring
- **StructuralConsent._save**: sem docstring
- **StructuralConsent.scope_for**: sem docstring
- **StructuralConsent.grant**: Grant (or, for OFF, revoke) consent for a workspace; persists
- **StructuralConsent.graph_path_for**: Resolve the consented graph path for ``workspace``, or None

## `conscio/structural_drift.py`
- **drift_path**: The drift-store path for an engine storage dir (CLI + daemon agree)
- **compute_delta**: Compare a persisted baseline against a fresh signal (PURE)
- **_clean_sha**: sem docstring
- **read_head_commit**: Read the current HEAD commit sha from ``root/``
- **compute_freshness**: Freshness of a graph (built at ``graph_commit``) vs the repo at ``root``
- **StructuralDigest.from_signal**: sem docstring
- **StructuralDigest.to_json**: sem docstring
- **StructuralDigest.from_json**: Rebuild from a stored dict; None if malformed (fail-tolerant)
- **StructuralDelta.changed**: Any real topology/content difference
- **StructuralDelta.summary**: sem docstring
- **StructuralDelta.to_advisory**: sem docstring
- **StructuralFreshness.known**: sem docstring
- **StructuralFreshness.is_stale**: True only when both commits are known AND they differ
- **StructuralFreshness.to_advisory**: sem docstring
- **StructuralDriftStore.__init__**: sem docstring
- **StructuralDriftStore._load**: sem docstring
- **StructuralDriftStore._save**: sem docstring
- **StructuralDriftStore.get**: sem docstring
- **StructuralDriftStore.put**: Advance the baseline for a workspace; persists (write failure swallowed)

## `conscio/timeutil.py`
- **naive_utcnow**: sem docstring
- **naive_utc_from_epoch**: Naive-UTC datetime from a Unix epoch (matches naive_utcnow()'s convention)

## `conscio/token_tracker.py`
- **TokenTracker.__init__**: sem docstring
- **TokenTracker._init_schema**: sem docstring
- **TokenTracker.estimate_tokens**: Estimate token count from text length (chars/4)
- **TokenTracker.record**: Record token usage for a source
- **TokenTracker.record_simple**: Record token usage from char counts (no text needed)
- **TokenTracker.gain**: Savings dashboard for the last N hours
- **TokenTracker.budget_status**: Daily budget status
- **TokenTracker.stats**: Overall tracker statistics
- **TokenTracker.compact**: Remove old token usage records
- **TokenTracker.close**: sem docstring

## `conscio/voice_preset.py`
- **resolve_voice_preset**: Return the preset name if its file exists; '' for 'none'/empty/missing
- **available_presets**: List installed preset names (file stems) in conscio/presets/voice/

## `conscio/workspace.py`
- **Workspace.recheck_each_cycle**: Whether the daemon should re-resolve the workspace every cycle
- **WorkspaceContext.__init__**: sem docstring
- **WorkspaceContext.current**: sem docstring
- **WorkspaceContext._resolve_root**: sem docstring
- **WorkspaceContext._git_root**: sem docstring
- **WorkspaceContext._workspace_id**: sem docstring
- **WorkspaceContext.classify_env**: sem docstring
- **WorkspaceContext.poll**: Re-resolve the workspace; emit workspace:changed if the id changed

## `conscio/world_model.py`
- **_clamp01**: sem docstring
- **WorldModel.__init__**: sem docstring
- **WorldModel._load**: Load world model from disk
- **WorldModel._save**: Save world model to disk
- **WorldModel.add_entity**: Add or update an entity in the world model
- **WorldModel.remove_entity**: Remove an entity and its relations
- **WorldModel.update_state**: Update an entity's state, appending to the bounded state_log only when the state actually changes (dedup consecutive identical)
- **WorldModel.get_entity**: Get an entity by name
- **WorldModel.list_entities**: Top-N entities by relevance (descending)
- **WorldModel.add_relation**: Add a relation between two entities
- **WorldModel.get_relations**: Get all relations involving an entity
- **WorldModel.list_relations**: All relations as a shallow-copied list (public read)
- **WorldModel.add_prediction**: Add a prediction about the world
- **WorldModel.get_predictions**: Get predictions, optionally filtered by keyword
- **WorldModel.validate_prediction**: Mark a prediction as validated (correct or incorrect)
- **WorldModel.query**: Natural language query against the world model
- **WorldModel.subgraph**: Get a subgraph around an entity, for context injection
- **WorldModel.stale_entities**: Find entities whose state hasn't been updated or whose relevance is low
- **WorldModel._boost_relevance**: Boost an entity's relevance (capped at 1)
- **WorldModel._compute_relevance**: Compute decayed relevance
- **WorldModel.entropy**: Entropy score in [0, 1] for an entity
- **WorldModel.decay_all_entities**: Recalculate relevance for all entities based on time decay
- **WorldModel.prune_irrelevant**: Remove entities below the minimum relevance threshold
- **WorldModel.prune_stale**: Decay relevance, then remove stale entities (and their relations)
- **WorldModel.prune_by_entropy**: Decay relevance, then remove entities whose entropy exceeds ``threshold``
- **WorldModel.recently_changed**: Names of entities whose ``last_updated`` is within the last ``hours``
- **WorldModel.record_prediction**: Record a world prediction outcome
- **WorldModel.recent_prediction_error_rate**: Fraction of recorded predictions in the window that were wrong (0)
- **WorldModel.to_dict**: Return the raw world model data
- **WorldModel.entity_count**: Total entities (public read — coherence touches no private state)
- **WorldModel.mark_contradictions**: Scan relations (per from→to pair) + entity state_logs via `detector`, writing a cached `contradicted: bool` onto each entity
- **WorldModel.contradicted_entities**: Cheap read of cached `contradicted` flags
- **WorldModel.status**: Return status for monitoring
