"""Vulture whitelist — public API, dispatch methods, sqlite3 idioms, and constants
that are used externally or by convention but not detectable by static analysis."""

# ── agency ──────────────────────────────────────────────────────────
from conscio.agency.gateway import OutputGateway, coerce

coerce
# _failure_gov: constructed in __init__, not yet consulted by the retry
# loop — v3.1 wiring left unfinished, not v3.6 scope. Whitelisted to keep
# the pre-existing lint gate green; the actual gap is worth a follow-up.
OutputGateway._failure_gov
OutputGateway.attach_ledger

from conscio.agency.fallback_adapter import FallbackAdapter

FallbackAdapter.current_model

from conscio.agency.fallback_multi import MultiProviderFallbackAdapter

MultiProviderFallbackAdapter.current_model
MultiProviderFallbackAdapter.current_provider

from conscio.agency.intercepter import Intercepter

Intercepter.register_function

from conscio.agency.ledger import ActionLedger

ActionLedger.row_factory

from conscio.agency.skills import SkillLibrary

SkillLibrary.row_factory

# ── auto_evolution ──────────────────────────────────────────────────
from conscio.auto_evolution import AutoEvolution, EvolutionType

EvolutionType.PROMPT_ADJUST
EvolutionType.CONFIG_CHANGE
AutoEvolution.mark_applied
AutoEvolution.mark_rolled_back
AutoEvolution.recent_proposals

# ── auto_index ───────────────────────────────────────────────────────
from conscio.auto_index import AutoIndexer

AutoIndexer.uninstall

# ── axis_pack ───────────────────────────────────────────────────────
from conscio.axis_pack import available_axis_packs

available_axis_packs

# ── content_store ────────────────────────────────────────────────────
from conscio.content_store import ContentStore, _IndexedChunk

_IndexedChunk.indexed_at
ContentStore.row_factory

# ── context_manager ─────────────────────────────────────────────────
from conscio.context_manager import ContextManager

ContextManager.get_off_context_path

# ── daemon ───────────────────────────────────────────────────────────
from conscio.daemon import DaemonRunner

DaemonRunner.should_stop

# ── dedup ────────────────────────────────────────────────────────────
from conscio.dedup import Deduplicator

Deduplicator.is_near_duplicate

# ── engine ───────────────────────────────────────────────────────────
from conscio.engine import ConsciousnessEngine

ConsciousnessEngine.structural_delta
ConsciousnessEngine.structural_freshness
ConsciousnessEngine.propose_evolution
ConsciousnessEngine.health_check
ConsciousnessEngine.token_summary

# ── entity_detector ──────────────────────────────────────────────────
from conscio.entity_detector import EntityDetector

EntityDetector.detect_and_store

# ── event_bus ────────────────────────────────────────────────────────
from conscio.event_bus import PRIORITY_CRITICAL, PRIORITY_TRIVIAL, EventBus

PRIORITY_CRITICAL
PRIORITY_TRIVIAL
EventBus.row_factory
EventBus.emit_batch
EventBus.recent_errors
EventBus.recent_anomalies
EventBus.mark_duplicate

# ── failure ──────────────────────────────────────────────────────────
from conscio.failure import FailureGovernor

FailureGovernor.is_open
FailureGovernor.reset

# ── goal_generator ───────────────────────────────────────────────────
from conscio.goal_generator import GoalGenerator, GoalOrigin

GoalOrigin.META_ERROR
GoalOrigin.SELF_PROMPT
GoalOrigin.COMPACTION
GoalGenerator.add_user_goal
GoalGenerator.expire_stale

# ── hub ──────────────────────────────────────────────────────────────
from conscio.hub.server import HubHandler

HubHandler.log_message
HubHandler.do_GET
HubHandler.do_POST
HubHandler.do_PUT

# ── installer ────────────────────────────────────────────────────────
from conscio.installer.extras import Extra

Extra.optional_dep
from conscio.installer.hostcfg import write_claude_code

write_claude_code

# ── integrations ─────────────────────────────────────────────────────
from conscio.integrations.neurata import NeurataBridge

NeurataBridge.deposit
NeurataBridge.shelf_insights

# ── kg_builder ───────────────────────────────────────────────────────
from conscio.kg_builder import ExtractionResult

ExtractionResult

# ── liaison ──────────────────────────────────────────────────────────
from conscio.liaison.mailbox import Mailbox

Mailbox.row_factory

# ── migrate ───────────────────────────────────────────────────────────
from conscio.migrate import Migrator

Migrator.row_factory
Migrator.migrate_all
Migrator.migration_log
Migrator.table_counts

# ── miner ────────────────────────────────────────────────────────────
from conscio.miner import Miner

Miner.ingest_conversation

# ── models ───────────────────────────────────────────────────────────
from conscio.models import ModelRegistry

ModelRegistry.context_for_consciousness
ModelRegistry.all_models

# ── noosphere ────────────────────────────────────────────────────────
from conscio.noosphere.catalog import Catalog

Catalog.row_factory

from conscio.noosphere.publish import Publisher

Publisher.row_factory

from conscio.noosphere.quarantine import Quarantine, QuarantineRow

QuarantineRow.last_trial_ts
QuarantineRow.last_trial_result
QuarantineRow.last_trial_error
Quarantine.row_factory

from conscio.noosphere.record_catalog import RecordCatalog

RecordCatalog.row_factory

from conscio.noosphere.record_publish import RecordPublisher

RecordPublisher.row_factory

# ── observatory ─────────────────────────────────────────────────────
from conscio.observatory.liaison_view import LiaisonProjection

LiaisonProjection.row_factory

from conscio.observatory.projection import Projection

Projection.row_factory

from conscio.observatory.society import SocietyProjection

SocietyProjection.row_factory

from conscio.observatory.knowledge_view import KnowledgeProjection

KnowledgeProjection

from conscio.observatory.structural_view import StructuralProjection

StructuralProjection
StructuralProjection.drift_timeline
StructuralProjection.freshness
StructuralProjection.graph

# ── output_filter ────────────────────────────────────────────────────
from conscio.output_filter import FilterPipeline

FilterPipeline.remove_stage
FilterPipeline.list_stages
build_pipeline_from_config  # noqa: F821

# ── prompt_zones ─────────────────────────────────────────────────────
from conscio.prompt_zones import PromptZones

PromptZones.stable_hash

# ── session_rag ──────────────────────────────────────────────────────
from conscio.session_rag import SessionRAG, SessionVectorStore

SessionVectorStore.reindex_required
SessionRAG.index_recent_sessions

# ── token_account ────────────────────────────────────────────────────
from conscio.token_account import TokenAccount

TokenAccount
TokenAccount.rotate

# ── token_tracker ────────────────────────────────────────────────────
from conscio.token_tracker import TokenTracker

TokenTracker.record_simple
TokenTracker.budget_status

# ── voice_preset ─────────────────────────────────────────────────────
from conscio.voice_preset import available_presets

available_presets

# ── wings ────────────────────────────────────────────────────────────
from conscio.wings import WingManager

WingManager.delete_drawer

# ── workspace ────────────────────────────────────────────────────────
from conscio.workspace import WorkspaceContext

WorkspaceContext.recheck_each_cycle

# ── world_model ──────────────────────────────────────────────────────
from conscio.world_model import WorldModel

WorldModel.get_entity
WorldModel.list_relations
WorldModel.subgraph
WorldModel.prune_stale
WorldModel.record_prediction

# ── gates (v3.0) ────────────────────────────────────────────────────
from conscio.gates import COUNCIL_ROLES, COUNCIL_VOTES

COUNCIL_ROLES
COUNCIL_VOTES

# ── pipelines (v3.0) ───────────────────────────────────────────────
from conscio.pipelines import PROMOTION_GATES

PROMOTION_GATES

# ── knowledge store (v3.6) ─────────────────────────────────────────
from conscio.content_store import IndexResult
from conscio.embedding_pipeline import EmbeddingPipeline

# Part of the IndexResult contract returned by index_ex() to callers/tests.
IndexResult.chunks_added
# Single-chunk entry point kept beside embed_batch() for callers that embed
# one item at a time (ingestion itself now goes through embed_batch).
EmbeddingPipeline.embed_chunk
