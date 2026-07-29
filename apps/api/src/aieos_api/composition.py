"""Explicit composition root for the executable AIEOS reference workflow."""

from dataclasses import dataclass

from aieos.adapters.ai_mock import MockAIGateway
from aieos.adapters.command_dispatch_in_process import InProcessCommandDispatcher
from aieos.adapters.event_bus_in_process import (
    InMemoryOutboxStore,
    InProcessEventBus,
    OutboxRelay,
)
from aieos.adapters.memory_persistence import InMemoryMemoryRepository
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.adapters.persistence_postgres import (
    BufferedPostgresOutbox,
    PostgresDatabase,
    PostgresDecisionEvidenceRepository,
    PostgresExecutionRepository,
    PostgresMemoryRepository,
    PostgresOutboxRelay,
    PostgresOutboxStore,
    PostgresRequestRepository,
    PostgresWorkflowRepository,
    TransactionParticipant,
    checkpoint,
    scoped_idempotency_lock_key,
)
from aieos.capability_registry import CapabilityImplementation, CapabilityRegistry
from aieos.contracts import AuthorizationContext, ResultEnvelope
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.domain import (
    Clock,
    DecisionEvidence,
    IdentifierFactory,
    InMemoryDecisionEvidenceRepository,
    SystemClock,
    UuidIdentifierFactory,
)
from aieos.event_bus import EventOutbox
from aieos.manager import InMemoryRequestRepository, Manager
from aieos.memory_service import MemoryService
from aieos.result_error_support import OutcomeFactory
from aieos.security_support import ScopeAuthorizer
from aieos.skill_registry import SkillDefinition, SkillRegistry
from aieos.skill_runtime import InMemoryExecutionRepository, SkillRuntime
from aieos.workflow_engine import (
    InMemoryWorkflowRepository,
    WorkflowClient,
    WorkflowEngine,
)
from aieos_api.reference_skill import HelloAIEOSSkill
from aieos_api.settings import HostSettings, RuntimeAdapter

FROZEN_RUNTIME_MODULES = (
    "Authentication",
    "Workspace",
    "Manager",
    "Workflow Engine",
    "Skill Registry",
    "Skill Runtime",
    "AI Gateway",
    "Memory Service",
    "Capability Registry",
    "Scheduler",
    "Analytics",
    "Notification",
    "Logging",
    "Configuration",
    "Command Dispatcher",
    "Event Bus",
)


@dataclass(frozen=True, slots=True)
class CompositionRoot:
    """Validated module registry and executable reference runtime."""

    settings: HostSettings
    modules: tuple[str, ...]
    reference_runtime: "ReferenceRuntime"
    database: PostgresDatabase | None = None

    def health(self) -> dict[str, object]:
        """Return startup readiness without disclosing configuration values."""
        return {"status": "ready", "module_count": len(self.modules)}

    async def readiness(self) -> dict[str, object]:
        migration = (
            {"ready": True, "status": "not_configured"}
            if self.database is None
            else await self.database.migration_readiness()
        )
        database_ready = bool(migration["ready"])
        return {
            "status": "ready" if database_ready else "not_ready",
            "database": "not_configured" if self.database is None else database_ready,
            "migration": migration,
        }

    async def close(self) -> None:
        if self.database is not None:
            await self.database.close()


class DispatchingWorkflowClient(WorkflowClient):
    """Manager-facing adapter that preserves directed Command dispatch."""

    def __init__(
        self, dispatcher: InProcessCommandDispatcher, workflow_engine: WorkflowEngine
    ) -> None:
        self._dispatcher = dispatcher
        self._workflow_engine = workflow_engine

    async def submit(self, command: CommandEnvelope) -> ResultEnvelope:
        return await self._dispatcher.dispatch(command)

    def outcome(self, workflow_id: str) -> ResultEnvelope | None:
        return self._workflow_engine.outcome(workflow_id)


@dataclass(slots=True)
class ReferenceRuntime:
    """Composition-owned facade for HelloAIEOSWorkflow."""

    settings: HostSettings
    dispatcher: InProcessCommandDispatcher
    workflow_engine: WorkflowEngine
    skill_runtime: SkillRuntime
    event_bus: InProcessEventBus
    outbox_store: InMemoryOutboxStore | PostgresOutboxStore
    outbox: EventOutbox
    memory_service: MemoryService
    memory_repository: InMemoryMemoryRepository | PostgresMemoryRepository
    ai_gateway: MockAIGateway
    observations: InMemoryObservationRecorder
    workflow_repository: InMemoryWorkflowRepository
    execution_repository: InMemoryExecutionRepository
    request_repository: InMemoryRequestRepository
    clock: Clock
    identifiers: IdentifierFactory
    authorization: AuthorizationContext
    decisions: InMemoryDecisionEvidenceRepository
    durable_participants: tuple[TransactionParticipant, ...] = ()
    database: PostgresDatabase | None = None

    async def run(
        self,
        message: str,
        *,
        command_id: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 2,
        timeout_seconds: float | None = None,
    ) -> ResultEnvelope:
        command = self.build_request_command(
            message,
            command_id=command_id,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
        )
        return await self.run_command(command)

    def build_request_command(
        self,
        message: str,
        *,
        command_id: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 2,
        timeout_seconds: float | None = None,
    ) -> CommandEnvelope:
        request_id = self.identifiers.new("request")
        decision_id = self.identifiers.new("decision")
        self.decisions.record(
            DecisionEvidence(
                decision_id=decision_id,
                decision_type="DispatchRequest",
                component="Reference Host",
                tenant_id=self.settings.tenant_id,
                workspace_id=self.settings.workspace_id,
                correlation_id=self.identifiers.new("correlation"),
                recorded_at=self.clock.now(),
                triggering_id=None,
            )
        )
        decision = self.decisions.decisions[decision_id]
        return CommandEnvelope(
            command_id=command_id or self.identifiers.new("command"),
            command_type="AcceptRequest",
            command_version="1.0",
            correlation_id=decision.correlation_id,
            causation_id=decision.decision_id,
            target_component="Manager",
            initiator="Reference Host",
            timestamp=self.clock.now(),
            tenant_id=self.settings.tenant_id,
            workspace_id=self.settings.workspace_id,
            payload={
                "message": message,
                "max_attempts": max_attempts,
                "timeout_seconds": timeout_seconds or self.settings.reference_timeout_seconds,
            },
            metadata=CommandMetadata(
                request_id=request_id,
                idempotency_key=idempotency_key or request_id,
                authorization=self.authorization,
            ),
        )

    async def run_command(self, command: CommandEnvelope) -> ResultEnvelope:
        if self.database is not None:
            async with self.database.command_lock(scoped_idempotency_lock_key(command)):
                for participant in self.durable_participants:
                    await participant.prepare()
                repository = self.request_repository
                assert isinstance(repository, PostgresRequestRepository)
                replay = await repository.replay_command(command)
                if replay is not None:
                    stored_command, completed_result = replay
                    if completed_result is not None:
                        return completed_result
                    command = stored_command
                return await self._run_prepared(command)
        return await self._run_prepared(command)

    async def _run_prepared(self, command: CommandEnvelope) -> ResultEnvelope:
        for participant in self.durable_participants:
            await participant.prepare()
        if self.database is not None:
            await self.outbox.drain()
        result = await self.dispatcher.dispatch(command)
        if self.database is not None:
            await checkpoint(self.database, self.durable_participants)
        return result


def compose(
    settings: HostSettings | None = None,
    *,
    clock: Clock | None = None,
    identifiers: IdentifierFactory | None = None,
) -> CompositionRoot:
    """Create the explicit modular-monolith composition root."""
    resolved = settings or HostSettings()
    resolved_clock = clock or SystemClock()
    resolved_identifiers = identifiers or UuidIdentifierFactory()
    outcomes = OutcomeFactory(resolved_clock, resolved_identifiers)
    authorizer = ScopeAuthorizer()
    authorization = AuthorizationContext(
        actor_id="reference-user",
        permissions=frozenset(
            {
                "request.accept",
                "workflow.start",
                "workflow.cancel",
                "skill.execute",
                "ai.invoke",
                "memory.write",
                "memory.read",
            }
        ),
        tenant_id=resolved.tenant_id,
        workspace_id=resolved.workspace_id,
        policy_id="reference-policy",
        policy_version_id="reference-policy-v1",
    )
    observations = InMemoryObservationRecorder(resolved_identifiers)
    dispatcher = InProcessCommandDispatcher()
    event_bus = InProcessEventBus()
    database = None
    if resolved.runtime_adapter is RuntimeAdapter.POSTGRES:
        assert resolved.database_url is not None
        database = PostgresDatabase(
            resolved.database_url.get_secret_value(),
            pool_size=resolved.database_pool_size,
            pool_timeout_seconds=resolved.database_pool_timeout_seconds,
            command_timeout_seconds=resolved.database_command_timeout_seconds,
        )
        postgres_outbox_store = PostgresOutboxStore(database)
        durable_scope = {
            "tenant_id": resolved.tenant_id,
            "workspace_id": resolved.workspace_id,
        }
        workflow_repository = PostgresWorkflowRepository(database, **durable_scope)
        execution_repository = PostgresExecutionRepository(database, **durable_scope)
        request_repository = PostgresRequestRepository(database, **durable_scope)
        decisions = PostgresDecisionEvidenceRepository(database, **durable_scope)
        memory_repository = PostgresMemoryRepository(database)
        durable_participants = (
            memory_repository,
            workflow_repository,
            execution_repository,
            request_repository,
            decisions,
        )
        outbox_store = postgres_outbox_store
        outbox = BufferedPostgresOutbox(
            postgres_outbox_store,
            PostgresOutboxRelay(
                postgres_outbox_store,
                event_bus,
                owner=resolved.host_name,
                batch_size=resolved.outbox_batch_size,
                lease_seconds=resolved.outbox_lease_seconds,
                backoff_seconds=resolved.delivery_backoff_seconds,
            ),
            participants=durable_participants,
        )
    else:
        memory_outbox_store = InMemoryOutboxStore()
        outbox_store = memory_outbox_store
        outbox = OutboxRelay(memory_outbox_store, event_bus)
        memory_repository = InMemoryMemoryRepository()
        workflow_repository = InMemoryWorkflowRepository()
        execution_repository = InMemoryExecutionRepository()
        request_repository = InMemoryRequestRepository()
        decisions = InMemoryDecisionEvidenceRepository()
        durable_participants = ()
    memory_service = MemoryService(
        repository=memory_repository,
        authorizer=authorizer,
        identifiers=resolved_identifiers,
        clock=resolved_clock,
    )
    ai_gateway = MockAIGateway(
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        authorizer=authorizer,
        failures_before_success=resolved.mock_ai_failures_before_success,
        delay_seconds=resolved.mock_ai_delay_seconds,
    )
    capabilities = CapabilityRegistry(
        (
            CapabilityImplementation(
                capability_id="text-generation",
                capability_contract_version_id="text-generation-v1",
                implementation_reference="hello-aieos-local",
                boundary="AI Gateway",
            ),
        )
    )
    skills = SkillRegistry(
        (
            SkillDefinition(
                skill_id="hello-aieos-skill",
                skill_version_id="hello-aieos-skill-v1",
                capability_id="text-generation",
                capability_contract_version_id="text-generation-v1",
                implementation_reference="hello-aieos-local",
            ),
        )
    )
    skill_runtime = SkillRuntime(
        repository=execution_repository,
        skills=skills,
        skill_implementations={"hello-aieos-local": HelloAIEOSSkill()},
        capabilities=capabilities,
        ai_gateway=ai_gateway,
        memory_service=memory_service,
        outbox=outbox,
        authorizer=authorizer,
        outcomes=outcomes,
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        observations=observations,
        default_timeout_seconds=resolved.reference_timeout_seconds,
    )
    workflow_engine = WorkflowEngine(
        repository=workflow_repository,
        dispatcher=dispatcher,
        outbox=outbox,
        authorizer=authorizer,
        outcomes=outcomes,
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        observations=observations,
        decisions=decisions,
    )
    workflow_client = DispatchingWorkflowClient(dispatcher, workflow_engine)
    manager = Manager(
        repository=request_repository,
        workflow_client=workflow_client,
        authorizer=authorizer,
        outcomes=outcomes,
        clock=resolved_clock,
        identifiers=resolved_identifiers,
    )
    dispatcher.register("Manager", manager)
    dispatcher.register("Workflow Engine", workflow_engine)
    dispatcher.register("Skill Runtime", skill_runtime)
    for event_type in (
        "ExecutionAttemptSucceeded",
        "ExecutionAttemptFailed",
        "ExecutionAttemptTimedOut",
    ):
        event_bus.subscribe(event_type, "workflow-engine", workflow_engine)
    runtime = ReferenceRuntime(
        settings=resolved,
        dispatcher=dispatcher,
        workflow_engine=workflow_engine,
        skill_runtime=skill_runtime,
        event_bus=event_bus,
        outbox_store=outbox_store,
        outbox=outbox,
        memory_service=memory_service,
        memory_repository=memory_repository,
        ai_gateway=ai_gateway,
        observations=observations,
        workflow_repository=workflow_repository,
        execution_repository=execution_repository,
        request_repository=request_repository,
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        authorization=authorization,
        decisions=decisions,
        durable_participants=durable_participants,
        database=database,
    )
    return CompositionRoot(resolved, FROZEN_RUNTIME_MODULES, runtime, database)
