"""Vulture whitelist — public API, dispatch methods, sqlite3 idioms, and constants
that are used externally or by convention but not detectable by static analysis."""

# ── agency ──────────────────────────────────────────────────────────
from conscio.agency.gateway import coerce
coerce
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

# ── engine ───────────────────────────────────────────────────────────
from conscio.engine import ConsciousnessEngine
ConsciousnessEngine.structural_delta
ConsciousnessEngine.structural_freshness
ConsciousnessEngine.propose_evolution
ConsciousnessEngine.health_check

# ── event_bus ────────────────────────────────────────────────────────
from conscio.event_bus import EventBus, Event, PRIORITY_CRITICAL, PRIORITY_TRIVIAL
PRIORITY_CRITICAL
PRIORITY_TRIVIAL
EventBus.row_factory
EventBus.emit_batch
EventBus.recent_errors
EventBus.recent_anomalies
EventBus.mark_duplicate

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

# ── liaison ──────────────────────────────────────────────────────────
from conscio.liaison.mailbox import Mailbox
Mailbox.row_factory

# ── migrate ───────────────────────────────────────────────────────────
from conscio.migrate import Migrator
Migrator.row_factory
Migrator.migrate_all
Migrator.migration_log
Migrator.table_counts

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

# ── output_filter ────────────────────────────────────────────────────
from conscio.output_filter import FilterPipeline
FilterPipeline.remove_stage
FilterPipeline.list_stages
build_pipeline_from_config  # noqa: F821

# ── session_rag ──────────────────────────────────────────────────────
from conscio.session_rag import SessionVectorStore, SessionRAG
SessionVectorStore.reindex_required
SessionRAG.index_recent_sessions

# ── token_tracker ────────────────────────────────────────────────────
from conscio.token_tracker import TokenTracker
TokenTracker.record_simple
TokenTracker.budget_status

# ── voice_preset ─────────────────────────────────────────────────────
from conscio.voice_preset import available_presets
available_presets

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
