"""Explicit composition root for the executable AIEOS reference workflow."""

from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal

from aieos.adapters.ai_mock import DeterministicMockProvider, MockAIGateway
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
    PostgresAIGatewayStore,
    PostgresDatabase,
    PostgresDecisionEvidenceRepository,
    PostgresExecutionRepository,
    PostgresMemoryRepository,
    PostgresOutboxRelay,
    PostgresOutboxStore,
    PostgresProviderEffectBoundary,
    PostgresRequestRepository,
    PostgresWorkflowRepository,
    TransactionParticipant,
    checkpoint,
    scoped_idempotency_lock_key,
    scoped_workflow_lock_key,
    workflow_lock_key,
)
from aieos.ai_gateway import (
    AIGateway,
    AIInvocationRequest,
    AIInvocationResponse,
    ModelCatalogEntry,
    PromptPackageCatalog,
    ReferenceAIGateway,
    ReferenceGatewayStore,
)
from aieos.capability_registry import CapabilityImplementation, CapabilityRegistry
from aieos.contracts import AuthorizationContext, DataClassification, ResultEnvelope
from aieos.contracts.commands import CommandEnvelope, CommandMetadata
from aieos.contracts.events import EventEnvelope
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
from aieos.skill_runtime import (
    STRUCTURED_TASK_KIND_PACKAGE,
    CapabilityPolicyContext,
    InMemoryExecutionRepository,
    SkillRuntime,
    StructuredTaskKindClassification,
)
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


class CapabilityGatewayRouter:
    """Select a composed Gateway implementation by governed capability identity."""

    def __init__(self, text_gateway: MockAIGateway, structured_gateway: AIGateway) -> None:
        self._text_gateway = text_gateway
        self._structured_gateway = structured_gateway

    @property
    def invocations(self) -> list[str]:
        """Preserve reference-host inspection of the existing text mock."""
        return self._text_gateway.invocations

    async def invoke(self, request: AIInvocationRequest) -> AIInvocationResponse:
        gateway = (
            self._structured_gateway
            if request.capability_id == "StructuredTaskKindClassification"
            else self._text_gateway
        )
        return await gateway.invoke(request)


class DurableWorkflowEventConsumer:
    """Serialize and checkpoint event-driven mutations for one exact Workflow."""

    def __init__(
        self,
        database: PostgresDatabase,
        repository: PostgresWorkflowRepository,
        workflow_engine: WorkflowEngine,
        participants: tuple[TransactionParticipant, ...],
    ) -> None:
        self._database = database
        self._repository = repository
        self._workflow_engine = workflow_engine
        self._participants = participants
        self._held_workflow_locks: ContextVar[frozenset[str]] = ContextVar(
            "held_workflow_locks", default=frozenset()
        )

    async def consume(self, event: EventEnvelope) -> None:
        if event.workflow_id is None or event.tenant_id is None or event.workspace_id is None:
            raise ValueError("durable Workflow Event requires exact scoped WorkflowId")
        # A pre-AI rejection can be raised after a child dispatch identity is
        # mutated.  Lock and load the Engine's immutable dispatched Workflow,
        # never the caller-controlled value carried by that rejected Event.
        workflow_id = self._workflow_engine.pre_acceptance_rejection_workflow_id(event)
        authoritative_workflow_id = workflow_id or event.workflow_id
        lock_key = workflow_lock_key(
            event.tenant_id,
            event.workspace_id,
            authoritative_workflow_id,
        )
        held = self._held_workflow_locks.get()
        if lock_key in held:
            await self._workflow_engine.consume(event)
            await checkpoint(self._database, self._participants)
            return
        async with self._database.command_lock(lock_key):
            token = self._held_workflow_locks.set(held | {lock_key})
            try:
                if await self._repository.refresh_workflow(authoritative_workflow_id) is None:
                    raise KeyError(f"Workflow does not exist: {authoritative_workflow_id}")
                await self._workflow_engine.consume(event)
                await checkpoint(self._database, self._participants)
            finally:
                self._held_workflow_locks.reset(token)


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
    ai_gateway: CapabilityGatewayRouter
    reference_ai_gateway: ReferenceAIGateway
    observations: InMemoryObservationRecorder
    workflow_repository: InMemoryWorkflowRepository
    execution_repository: InMemoryExecutionRepository
    request_repository: InMemoryRequestRepository
    clock: Clock
    identifiers: IdentifierFactory
    authorization: AuthorizationContext
    authorizer: ScopeAuthorizer
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

    async def classify_and_route_task(
        self, statement: str, *, budget_ceiling: str = "0.01", command_id: str | None = None
    ) -> ResultEnvelope:
        """Run the sole Phase 6 reference Workflow; routes are terminal values."""
        request_id = self.identifiers.new("request")
        command = CommandEnvelope(
            command_id=command_id or self.identifiers.new("command"),
            command_type="StartWorkflow",
            command_version="2.0",
            correlation_id=self.identifiers.new("correlation"),
            causation_id=request_id,
            target_component="Workflow Engine",
            initiator="Reference Host",
            timestamp=self.clock.now(),
            tenant_id=self.settings.tenant_id,
            workspace_id=self.settings.workspace_id,
            payload={
                "workflow_definition_id": "ClassifyAndRouteTask",
                "workflow_definition_version_id": "classify-and-route-task-v1",
                "workflow_kind": "ClassifyAndRouteTask",
                "skill_version_id": "structured-task-kind-skill-v1",
                "statement": statement,
                "max_attempts": 1,
                "workflow_ai_budget_envelope": {
                    "ContractVersion": 1,
                    "GatewayNormalizedCostUnitRegistryVersion": 1,
                    "WorkflowDefinitionVersionId": "classify-and-route-task-v1",
                    "PolicyId": self.authorization.policy_id,
                    "PolicyVersionId": self.authorization.policy_version_id,
                    "TenantId": self.settings.tenant_id,
                    "WorkspaceId": self.settings.workspace_id,
                    "BudgetCeiling": {"Amount": budget_ceiling, "CurrencyOrReferenceUnit": "USD"},
                },
            },
            metadata=CommandMetadata(
                request_id=request_id, idempotency_key=request_id, authorization=self.authorization
            ),
        )
        return await self.run_workflow_command(command)

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

    async def run_execution_command(self, command: CommandEnvelope) -> ResultEnvelope:
        """Dispatch one governed Skill Runtime command with the durable host checkpoint."""
        if command.target_component != "Skill Runtime":
            raise ValueError("execution command must target Skill Runtime")
        if self.database is not None:
            async with self.database.command_lock(scoped_idempotency_lock_key(command)):
                for participant in self.durable_participants:
                    await participant.prepare()
                receipt = self.execution_repository.receipt_for_command(command.command_id)
                if receipt is not None and receipt.completed:
                    if receipt.command != command:
                        raise ValueError(
                            "CommandId cannot be reused with changed immutable content"
                        )
                    return receipt.acknowledgement
                result = await self.dispatcher.dispatch(command)
                await checkpoint(self.database, self.durable_participants)
                return result
        return await self.dispatcher.dispatch(command)

    async def run_workflow_command(self, command: CommandEnvelope) -> ResultEnvelope:
        """Dispatch one governed Workflow Engine command with the durable host checkpoint."""
        if command.target_component != "Workflow Engine":
            raise ValueError("workflow command must target Workflow Engine")
        if self.database is not None:
            async with self.database.command_lock(scoped_idempotency_lock_key(command)):
                workflow_lock = scoped_workflow_lock_key(command)
                if workflow_lock is not None:
                    async with self.database.command_lock(workflow_lock):
                        return await self._run_workflow_prepared(command)
                return await self._run_workflow_prepared(command)
        return await self.dispatcher.dispatch(command)

    async def _run_workflow_prepared(self, command: CommandEnvelope) -> ResultEnvelope:
        for participant in self.durable_participants:
            await participant.prepare()
        repository = self.workflow_repository
        assert isinstance(repository, PostgresWorkflowRepository)
        replay = await repository.replay_command(command)
        if replay is not None:
            stored_command, completed_result = replay
            if completed_result is not None:
                return completed_result
            command = stored_command
        result = await self.dispatcher.dispatch(command)
        assert self.database is not None
        await checkpoint(self.database, self.durable_participants)
        return result

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
                "result.read",
            }
        ),
        tenant_id=resolved.tenant_id,
        workspace_id=resolved.workspace_id,
        policy_id="reference-policy",
        policy_version_id="reference-policy-v1",
    )
    authorizer = ScopeAuthorizer(
        active_policy_versions={
            (
                resolved.tenant_id,
                resolved.workspace_id,
                authorization.policy_id,
                authorization.policy_version_id,
            )
        }
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
        postgres_outbox_store = PostgresOutboxStore(
            database,
            required_consumers={
                "ExecutionAttemptSucceeded": ("workflow-engine",),
                "ExecutionAttemptFailed": ("workflow-engine",),
                "ExecutionAttemptTimedOut": ("workflow-engine",),
            },
        )
        durable_scope = {
            "tenant_id": resolved.tenant_id,
            "workspace_id": resolved.workspace_id,
        }
        workflow_repository = PostgresWorkflowRepository(database, **durable_scope)
        execution_repository = PostgresExecutionRepository(database, **durable_scope)
        request_repository = PostgresRequestRepository(database, **durable_scope)
        decisions = PostgresDecisionEvidenceRepository(database, **durable_scope)
        memory_repository = PostgresMemoryRepository(database)
        gateway_store = PostgresAIGatewayStore(database)
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
        gateway_store = ReferenceGatewayStore()
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
    prompt_packages = PromptPackageCatalog((STRUCTURED_TASK_KIND_PACKAGE,))
    reference_ai_adapters = {
        "mock-economy": DeterministicMockProvider("mock-economy", prefix="Economy"),
        "mock-quality": DeterministicMockProvider("mock-quality", prefix="Quality"),
    }
    if database is not None:
        provider_effect_boundary = PostgresProviderEffectBoundary(database)
        for adapter in reference_ai_adapters.values():
            adapter.use_effect_boundary(provider_effect_boundary)
    reference_ai_gateway = ReferenceAIGateway(
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        authorizer=authorizer,
        observations=observations,
        store=gateway_store,
        catalog=(
            ModelCatalogEntry(
                model_key="economy-text-v1",
                adapter_key="mock-economy",
                capabilities=frozenset({"text", "structured", "stream"}),
                context_limit=4096,
                max_output=1024,
                quality_tier=1,
                latency_tier=1,
                input_cost_per_token=Decimal("0.000001"),
                output_cost_per_token=Decimal("0.000002"),
                pricing_version="reference-2026-08",
            ),
            ModelCatalogEntry(
                model_key="quality-text-v1",
                adapter_key="mock-quality",
                capabilities=frozenset({"text", "structured", "stream", "reasoning"}),
                context_limit=16384,
                max_output=4096,
                quality_tier=3,
                latency_tier=2,
                input_cost_per_token=Decimal("0.000004"),
                output_cost_per_token=Decimal("0.000008"),
                pricing_version="reference-2026-08",
            ),
        ),
        adapters=reference_ai_adapters,
        prompt_packages=prompt_packages,
        workflow_admission_authority=workflow_repository,
    )
    routed_ai_gateway = CapabilityGatewayRouter(ai_gateway, reference_ai_gateway)
    capabilities = CapabilityRegistry(
        (
            CapabilityImplementation(
                capability_id="text-generation",
                capability_contract_version_id="text-generation-v1",
                implementation_reference="hello-aieos-local",
                boundary="AI Gateway",
            ),
            CapabilityImplementation(
                capability_id="StructuredTaskKindClassification",
                capability_contract_version_id="1",
                implementation_reference="structured-task-kind-local",
                boundary="AI Gateway",
                prompt_package_ref="structured-task-kind",
                prompt_package_version_ref="v1",
                output_schema_ref="structured-task-kind-schema-v1",
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
            SkillDefinition(
                skill_id="structured-task-kind-skill",
                skill_version_id="structured-task-kind-skill-v1",
                capability_id="StructuredTaskKindClassification",
                capability_contract_version_id="1",
                implementation_reference="structured-task-kind-local",
            ),
        )
    )
    skill_runtime = SkillRuntime(
        repository=execution_repository,
        skills=skills,
        skill_implementations={
            "hello-aieos-local": HelloAIEOSSkill(),
            "structured-task-kind-local": StructuredTaskKindClassification(
                prompt_packages=prompt_packages,
                policy_context=CapabilityPolicyContext(
                    data_classification=DataClassification.INTERNAL,
                    safety_policy_ref="reference-safety-v1",
                    cache_policy_ref="no-store",
                    budget_policy_ref="reference-budget-v1",
                    residency="any",
                    required_data_handling=frozenset(),
                    minimum_security_tier=1,
                ),
            ),
        },
        capabilities=capabilities,
        ai_gateway=routed_ai_gateway,
        memory_service=memory_service,
        outbox=outbox,
        authorizer=authorizer,
        outcomes=outcomes,
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        observations=observations,
        workflow_admission_authority=workflow_repository,
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
    if database is not None:
        assert isinstance(workflow_repository, PostgresWorkflowRepository)
        workflow_event_consumer = DurableWorkflowEventConsumer(
            database,
            workflow_repository,
            workflow_engine,
            durable_participants,
        )
    else:
        workflow_event_consumer = workflow_engine
    for event_type in (
        "ExecutionAttemptSucceeded",
        "ExecutionAttemptFailed",
        "ExecutionAttemptTimedOut",
    ):
        event_bus.subscribe(event_type, "workflow-engine", workflow_event_consumer)
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
        ai_gateway=routed_ai_gateway,
        reference_ai_gateway=reference_ai_gateway,
        observations=observations,
        workflow_repository=workflow_repository,
        execution_repository=execution_repository,
        request_repository=request_repository,
        clock=resolved_clock,
        identifiers=resolved_identifiers,
        authorization=authorization,
        authorizer=authorizer,
        decisions=decisions,
        durable_participants=durable_participants,
        database=database,
    )
    return CompositionRoot(resolved, FROZEN_RUNTIME_MODULES, runtime, database)
