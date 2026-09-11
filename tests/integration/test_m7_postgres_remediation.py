"""Mandatory live PG17 evidence: transactions, collisions, recovery, and source union."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aieos.adapters.persistence_postgres.database import PostgresDatabase
from aieos.adapters.persistence_postgres.employee import PostgresEmployeePersistence, Scope
from aieos.adapters.persistence_postgres.employee_codec import (
    UnsafeM7CommandValue,
    encode_safe_value,
    encode_start_workflow_evidence,
)
from aieos.adapters.persistence_postgres.employee_references import DurableReference
from aieos.adapters.persistence_postgres.employee_response import WorkflowResponse
from aieos.contracts.commands.models import CommandEnvelope, CommandMetadata
from aieos.contracts.common import AuthorizationContext
from aieos.contracts.results import (
    ErrorCategory,
    ErrorEnvelope,
    ErrorSeverity,
    OutcomeCategory,
    ResultEnvelope,
    ResultStatus,
    RetryClassification,
)
from aieos.workflow_engine.engine import (
    CommandProcessingState,
    WorkflowCommandReceipt,
    WorkflowDefinition,
    WorkflowInstance,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres_required, pytest.mark.anyio]
SCOPE = Scope("m7-tenant", "m7-workspace")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database() -> AsyncIterator[PostgresDatabase]:
    url = os.environ.get("AIEOS_TEST_DATABASE_URL")
    if url is None:
        if os.environ.get("CI"):
            pytest.fail("mandatory PostgreSQL configuration missing")
        pytest.skip("live PostgreSQL unavailable")
    db = PostgresDatabase(url)
    async with db.transaction() as session:
        # Exercise real migrated tables; never mock execute() or create replacement schema.
        await session.execute(
            text("""TRUNCATE command_idempotency, employee_admissions, employee_manager_handoffs,
            employee_manager_receipts, employee_start_workflow_commands, employee_observations,
            employee_observation_conflicts, employee_conflict_resolutions,
            employee_durable_references,
            employee_projection_checkpoints, employee_administrative_heads CASCADE""")
        )
    try:
        yield db
    finally:
        await db.close()


def start(command_id: str = "start-1", key: str = "start-key") -> CommandEnvelope:
    return CommandEnvelope(
        command_id,
        "StartWorkflow",
        "1.0",
        "correlation",
        "decision-1",
        "Workflow Engine",
        "Manager",
        datetime(2026, 9, 9, tzinfo=UTC),
        SCOPE.tenant_id,
        SCOPE.workspace_id,
        {
            "workflow_definition_id": "definition",
            "workflow_definition_version_id": "version",
            "skill_version_id": "skill",
            "nested": [None, {"value": 1}],
        },
        CommandMetadata(
            "request",
            key,
            AuthorizationContext(
                "actor",
                frozenset({"workflow.start"}),
                SCOPE.tenant_id,
                SCOPE.workspace_id,
                "policy",
                "v1",
            ),
        ),
    )


def decision() -> bytes:
    return encode_safe_value(
        {
            "managerRequestDecisionId": "decision-1",
            "requestId": "request",
            "selectedWorkflowDefinitionVersionId": "version",
            "selectionEvidence": {"source": "Manager", "version": "1"},
        }
    )


async def admit(repo: PostgresEmployeePersistence, command: CommandEnvelope | None = None) -> str:
    result = await repo.commit_admission_with_pending_command(
        SCOPE,
        "principal",
        "admission-key",
        b"exact caller basis",
        "execution",
        "snapshot",
        "manager-1",
        "exact snapshot evidence",
        "Manager",
        "manager-key",
        encode_safe_value({"commandId": "manager-1", "input": "exact"}),
        decision(),
        command or start(),
    )
    return result.outcome


async def test_atomic_commit_restart_same_command_and_fingerprint_non_authority(
    database: PostgresDatabase,
) -> None:
    async with database.transaction() as session:
        assert await admit(PostgresEmployeePersistence(session)) == "Created"
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        assert await admit(repo) == "Existing"
        recovered = await repo.load_recovery(SCOPE, "Manager", "manager-1")
        assert recovered.outcome == "Recovered" and recovered.value is not None
        assert recovered.value["start_command"] == start()
        assert recovered.value["start_command_id"] == "start-1"
        assert recovered.value["start_key"] == "start-key"
        assert recovered.value["idempotency_key"] == "manager-key"
        result = await repo.save_start_workflow_command(
            SCOPE, "start-1", "start-key", encode_start_workflow_evidence(start()), "different hint"
        )
        assert result.outcome == "Existing"
        assert (
            await repo.load_recovery(Scope(SCOPE.tenant_id, "other"), "Manager", "manager-1")
        ).outcome == "NotFound"


async def test_outer_transaction_rollback_leaves_no_lifecycle_rows(
    database: PostgresDatabase,
) -> None:
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            assert await admit(PostgresEmployeePersistence(session)) == "Created"
            raise RuntimeError("crash before commit")
    async with database.transaction() as session:
        for table in (
            "employee_admissions",
            "employee_manager_receipts",
            "employee_start_workflow_commands",
            "employee_manager_handoffs",
        ):
            assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.parametrize("change", ["id", "key", "trace", "timestamp", "payload"])
async def test_changed_command_conflicts_without_replacement(
    database: PostgresDatabase, change: str
) -> None:
    original = start()
    candidate = {
        "id": replace(original, command_id="different"),
        "key": replace(original, metadata=replace(original.metadata, idempotency_key="different")),
        "trace": replace(original, metadata=replace(original.metadata, trace_id="different")),
        "timestamp": replace(original, timestamp=datetime(2026, 9, 10, tzinfo=UTC)),
        "payload": replace(original, payload={**original.payload, "extra": None}),
    }[change]
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        assert (
            await repo.save_start_workflow_command(
                SCOPE,
                original.command_id,
                original.metadata.idempotency_key,
                encode_start_workflow_evidence(original),
                "same hint",
            )
        ).outcome == "Created"
        assert (
            await repo.save_start_workflow_command(
                SCOPE,
                candidate.command_id,
                candidate.metadata.idempotency_key,
                encode_start_workflow_evidence(candidate),
                "same hint",
            )
        ).outcome == "IdentityConflict"
        assert (
            await session.scalar(text("SELECT count(*) FROM employee_start_workflow_commands")) == 1
        )


async def test_inner_collision_rolls_back_admission_and_receipt(database: PostgresDatabase) -> None:
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        collision = start("foreign", "start-key")
        await repo.save_start_workflow_command(
            SCOPE, "foreign", "start-key", encode_start_workflow_evidence(collision), None
        )
        assert await admit(repo) == "IdentityConflict"
        assert await session.scalar(text("SELECT count(*) FROM employee_admissions")) == 0
        assert await session.scalar(text("SELECT count(*) FROM employee_manager_receipts")) == 0


async def test_manager_key_collision_is_deterministic(database: PostgresDatabase) -> None:
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        evidence = encode_safe_value({"commandId": "manager-1"})
        assert (
            await repo.open_manager_receipt(
                SCOPE, "Manager", "manager-1", "key", "execution", evidence, "hint"
            )
        ).outcome == "Opened"
        assert (
            await repo.open_manager_receipt(
                SCOPE, "Manager", "other", "key", "execution", evidence, "hint"
            )
        ).outcome == "IdentityConflict"
        assert (
            await repo.open_manager_receipt(
                SCOPE, "Manager", "manager-1", "key", "execution", evidence, "other hint"
            )
        ).outcome == "Existing"


class Owner:
    def __init__(self, evidence: bytes) -> None:
        self.evidence = evidence
        self.calls = 0

    async def validate_use(self, reference: DurableReference, purpose: str) -> bytes:
        self.calls += 1
        if (
            reference.owning_contract != "Owner"
            or reference.contract_version != "1"
            or reference.classification != "Internal"
            or reference.provenance != "source-1"
            or purpose != "WorkflowInput"
        ):
            raise UnsafeM7CommandValue("owner rejects current reference eligibility")
        return self.evidence


def reference() -> DurableReference:
    return DurableReference(
        SCOPE.tenant_id,
        SCOPE.workspace_id,
        "Input",
        "input-1",
        "Owner",
        "1",
        "immutable-v1",
        "source-1",
        "Internal",
        "WorkflowInput",
        encode_safe_value({"revision": "1", "scope": "exact", "pin": "immutable-v1"}),
    )


async def test_durable_reference_commit_restart_and_owner_revalidation(
    database: PostgresDatabase,
) -> None:
    ref = reference()
    owner = Owner(ref.owner_evidence)
    async with database.transaction() as session:
        assert (
            await PostgresEmployeePersistence(session).commit_durable_reference(SCOPE, ref, owner)
        ).outcome == "Created"
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        assert await repo.revalidate_durable_reference(SCOPE, ref, "WorkflowInput", owner) == ref
        owner.evidence = encode_safe_value({"revision": "2"})
        with pytest.raises(UnsafeM7CommandValue):
            await repo.revalidate_durable_reference(SCOPE, ref, "WorkflowInput", owner)
    assert owner.calls == 3


@pytest.mark.parametrize(
    "change", ["scope", "purpose", "pin", "owner", "version", "provenance", "classification"]
)
async def test_reference_fail_closed(database: PostgresDatabase, change: str) -> None:
    ref = reference()
    owner = Owner(ref.owner_evidence)
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await repo.commit_durable_reference(SCOPE, ref, owner)
        changed = {
            "scope": replace(ref, workspace_id="other"),
            "purpose": ref,
            "pin": replace(ref, immutable_pin="changed"),
            "owner": replace(ref, owning_contract="other"),
            "version": replace(ref, contract_version="2"),
            "provenance": replace(ref, provenance="forged"),
            "classification": replace(ref, classification="Public"),
        }[change]
        with pytest.raises(UnsafeM7CommandValue):
            await repo.revalidate_durable_reference(
                SCOPE, changed, "WrongPurpose" if change == "purpose" else "WorkflowInput", owner
            )


def response(tag: str) -> WorkflowResponse:
    status, category = (
        (ResultStatus.ACCEPTED, OutcomeCategory.ACKNOWLEDGEMENT)
        if tag == "Acknowledged"
        else (ResultStatus.REJECTED, OutcomeCategory.REJECTION)
    )
    source = ResultEnvelope(
        "result-1",
        status,
        category,
        "workflow-1" if tag in {"Acknowledged", "TerminalWithWorkflow"} else "start-1",
        SCOPE.tenant_id,
        SCOPE.workspace_id,
        "correlation",
        "start-1",
        "Workflow Engine",
        command_id="start-1",
        value_reference="workflow-1" if tag == "Acknowledged" else None,
        completed_at=None if tag == "Acknowledged" else datetime(2026, 9, 9, tzinfo=UTC),
        error_id=None if tag == "Acknowledged" else "error-1",
    )
    has_workflow = tag in {"Acknowledged", "TerminalWithWorkflow"}
    proof = (
        encode_safe_value(
            {
                "tenantId": SCOPE.tenant_id,
                "workspaceId": SCOPE.workspace_id,
                "startWorkflowCommandId": "start-1",
                "sourceResultId": "result-1",
                "workflowId": "workflow-1",
                "sourceEvidence": TypeAdapter(WorkflowCommandReceipt)
                .dump_json(make_source_receipt(tag, source))
                .decode("utf-8"),
            }
        )
        if has_workflow
        else None
    )
    return WorkflowResponse(
        tag,
        TypeAdapter(ResultEnvelope).dump_json(source),
        "a" * 64,
        "M6-StartWorkflow-52271c4-v1",
        None
        if tag == "Acknowledged"
        else encode_safe_value({"errorId": "error-1", "digest": "error-digest"}),
        "workflow-1" if has_workflow else None,
        proof,
    )


@pytest.mark.parametrize(
    "tag",
    [
        "Acknowledged",
        "RejectedNoWorkflow",
        "TerminalWithWorkflow",
        "TerminalCapturedIdentityUnresolved",
    ],
)
async def test_response_union_persists_and_observation_catches_up(
    database: PostgresDatabase, tag: str
) -> None:
    item = response(tag)
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        await stage_source(session, item)
        assert (await repo.capture_response(SCOPE, "start-1", item)).outcome == "ResponseCaptured"
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        read = await repo.read_response(SCOPE, "start-1")
        assert read.value == {"response": item}
        assert (await repo.capture_response(SCOPE, "start-1", item)).outcome == "ExistingResponse"
        assert (
            await repo.confirm_employee_observation(SCOPE, "start-1", item.encode())
        ).outcome == "Confirmed"
        assert (await repo.read_response(SCOPE, "start-1")).value == {"response": item}
        row = (
            await session.execute(
                text(
                    "SELECT response_tag,workflow_id FROM employee_observations "
                    "WHERE observation_kind='WorkflowStartResponse'"
                )
            )
        ).one()
        assert tuple(row) == (tag, item.workflow_id)


async def test_response_conflict_retains_both_evidences_and_blocks_observation(
    database: PostgresDatabase,
) -> None:
    item = response("Acknowledged")
    other = replace(item, source_result_digest="b" * 64)
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        await stage_source(session, item)
        await repo.capture_response(SCOPE, "start-1", item)
        conflict = await repo.capture_response(SCOPE, "start-1", other)
        assert conflict.outcome == "QuarantinedConflict" and conflict.value is not None
        conflict_id = str(conflict.value["conflict_id"])
        assert (
            await repo.capture_response(SCOPE, "start-1", item)
        ).outcome == "QuarantinedConflict"
        assert (
            await repo.confirm_employee_observation(SCOPE, "start-1", item.encode())
        ).outcome == "IntegrityConflict"
        row = (
            await session.execute(
                text(
                    "SELECT existing_evidence,incoming_evidence FROM employee_observation_conflicts"
                )
            )
        ).one()
        assert tuple(row) == (item.encode(), other.encode())
        assert (
            await repo.resolve_conflict(
                SCOPE, conflict_id, b"verified source resolution", "result-1"
            )
        ).outcome == "Resolved"
        assert (
            await repo.resolve_conflict(
                SCOPE, conflict_id, b"verified source resolution", "result-1"
            )
        ).outcome == "ExistingResolution"
        assert (
            await repo.resolve_conflict(SCOPE, conflict_id, b"changed", "result-1")
        ).outcome == "IntegrityConflict"
        assert (
            await repo.resolve_conflict(SCOPE, "missing", b"resolution", "result-1")
        ).outcome == "NotFound"


async def test_checkpoint_admin_and_receipt_cas_fencing(database: PostgresDatabase) -> None:
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        assert (
            await repo.transition_receipt(
                SCOPE, "Manager", "manager-1", 0, 0, "CommandCreatedDispatchPending"
            )
        ).outcome == "FenceLost"
        await session.execute(
            text("""INSERT INTO employee_projection_checkpoints VALUES
            ('m7-tenant','m7-workspace','view','execution',0,2,'basis','Pending')""")
        )
        assert (
            await repo.commit_checkpoint(SCOPE, "view", "execution", 0, 1, b"new", "Complete")
        ).outcome == "FenceLost"
        assert (
            await repo.commit_checkpoint(SCOPE, "view", "execution", 0, 2, b"new", "Complete")
        ).outcome == "Committed"
        assert (
            await repo.commit_checkpoint(SCOPE, "view", "execution", 0, 2, b"stale", "Complete")
        ).outcome == "FenceLost"
        await session.execute(
            text("""INSERT INTO employee_administrative_heads VALUES
            ('m7-tenant','m7-workspace','Assignment','version',0,'evidence','Eligible')""")
        )
        assert (
            await repo.advance_administrative_head(
                SCOPE, "Assignment", "version", 0, b"revoked", "Ineligible"
            )
        ).outcome == "Advanced"
        assert (
            await repo.advance_administrative_head(
                SCOPE, "Assignment", "version", 0, b"stale", "Eligible"
            )
        ).outcome == "StaleRevision"


async def stage_source(session: AsyncSession, item: WorkflowResponse) -> None:
    source = TypeAdapter(ResultEnvelope).validate_json(item.source_result)
    receipt = make_source_receipt(item.tag, source)
    if item.workflow_id is not None:
        command = start()
        instance = WorkflowInstance(
            "workflow-1",
            "step-1",
            WorkflowDefinition("definition", "version", "skill"),
            SCOPE.tenant_id,
            SCOPE.workspace_id,
            "request",
            "correlation",
            command.metadata.authorization,
            command.payload,
            1.0,
        )
        await session.execute(
            text("""INSERT INTO workflows
            (tenant_id,workspace_id,workflow_id,state,version,correlation_id,payload)
            VALUES (:t,:w,'workflow-1','Running',1,'correlation',:p)
            ON CONFLICT (tenant_id,workspace_id,workflow_id) DO UPDATE SET payload=:p"""),
            {
                "t": SCOPE.tenant_id,
                "w": SCOPE.workspace_id,
                "p": TypeAdapter(WorkflowInstance).dump_json(instance),
            },
        )
    await session.execute(
        text("""INSERT INTO command_idempotency
        (tenant_id,workspace_id,target_component,idempotency_key,command_id,command_hash,completed,payload)
        VALUES (:t,:w,'Workflow Engine','start-key','start-1','non-authoritative',true,:e)
        ON CONFLICT (tenant_id,workspace_id,target_component,idempotency_key)
        DO UPDATE SET payload=:e"""),
        {
            "t": SCOPE.tenant_id,
            "w": SCOPE.workspace_id,
            "e": TypeAdapter(WorkflowCommandReceipt).dump_json(receipt),
        },
    )


async def test_recovery_revalidates_command_bound_references(database: PostgresDatabase) -> None:
    from aieos.adapters.persistence_postgres.employee_codec import decode_safe_value
    from aieos.adapters.persistence_postgres.employee_references import encode_durable_reference

    ref = reference()
    owner = Owner(ref.owner_evidence)
    owners = {("Owner", "1"): owner}
    original = start()
    original = replace(
        original,
        payload={
            **original.payload,
            "approved_input": decode_safe_value(encode_durable_reference(ref)),
        },
    )
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session, owners)
        await repo.open_manager_receipt(
            SCOPE,
            "Manager",
            "manager-1",
            "manager-key",
            "execution",
            encode_safe_value({"commandId": "manager-1"}),
            None,
        )
        result = await repo.commit_manager_decision_and_pending_command(
            SCOPE, "Manager", "manager-1", 0, 0, decision(), original, {"approved_input": ref}
        )
        assert result.outcome == "Created"
    async with database.transaction() as session:
        assert (
            await PostgresEmployeePersistence(session).load_recovery(SCOPE, "Manager", "manager-1")
        ).outcome == "DependencyUnavailable"
        repo = PostgresEmployeePersistence(session, owners)
        recovered = await repo.load_recovery(SCOPE, "Manager", "manager-1")
        assert recovered.outcome == "Recovered" and recovered.value is not None
        assert recovered.value["start_command"] == original
        owner.evidence = encode_safe_value({"changed": "pin"})
        assert (
            await repo.load_recovery(SCOPE, "Manager", "manager-1")
        ).outcome == "IntegrityConflict"


@pytest.mark.parametrize("field", ["complete_evidence", "command_profile", "replay_path"])
async def test_corrupted_recovery_never_synthesizes_a_command(
    database: PostgresDatabase, field: str
) -> None:
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        value = b"o0:" if field == "complete_evidence" else "unknown"
        await session.execute(
            text(f"UPDATE employee_start_workflow_commands SET {field}=:v"), {"v": value}
        )
        result = await repo.load_recovery(SCOPE, "Manager", "manager-1")
        assert result.outcome in {"IntegrityConflict", "UnsupportedVersion"}
        assert result.value is None
        assert (
            await session.scalar(text("SELECT count(*) FROM employee_start_workflow_commands")) == 1
        )


async def test_concurrent_same_key_different_command_collision(database: PostgresDatabase) -> None:
    import asyncio

    async def save(command_id: str) -> str:
        command = start(command_id, "shared-key")
        async with database.transaction() as session:
            result = await PostgresEmployeePersistence(session).save_start_workflow_command(
                SCOPE,
                command.command_id,
                command.metadata.idempotency_key,
                encode_start_workflow_evidence(command),
                None,
            )
            return result.outcome

    assert sorted(await asyncio.gather(save("one"), save("two"))) == ["Created", "IdentityConflict"]


async def test_concurrent_same_command_converges(database: PostgresDatabase) -> None:
    import asyncio

    async def save() -> str:
        command = start()
        async with database.transaction() as session:
            result = await PostgresEmployeePersistence(session).save_start_workflow_command(
                SCOPE,
                command.command_id,
                command.metadata.idempotency_key,
                encode_start_workflow_evidence(command),
                None,
            )
            return result.outcome

    assert sorted(await asyncio.gather(save(), save())) == ["Created", "Existing"]


async def test_unproven_response_and_terminal_dispatch_closure(database: PostgresDatabase) -> None:
    item = response("RejectedNoWorkflow")
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        assert (
            await repo.capture_response(SCOPE, "start-1", item)
        ).outcome == "DependencyUnavailable"
        await stage_source(session, item)
        await repo.capture_response(SCOPE, "start-1", item)
        recovered = await repo.load_recovery(SCOPE, "Manager", "manager-1")
        assert recovered.value is not None and recovered.value["dispatch_allowed"] is False
        assert (
            await repo.transition_receipt(
                SCOPE, "Manager", "manager-1", 2, 0, "CommandCreatedDispatchPending"
            )
        ).outcome == "FenceLost"


@pytest.mark.parametrize("tag", ["Acknowledged", "TerminalWithWorkflow"])
async def test_missing_workflow_proof_rejected(database: PostgresDatabase, tag: str) -> None:
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        item = replace(response(tag), workflow_identity_proof=None)
        with pytest.raises(UnsafeM7CommandValue):
            await repo.capture_response(SCOPE, "start-1", item)
        assert await session.scalar(text("SELECT count(*) FROM employee_observations")) == 0


@pytest.mark.parametrize("accepted", [True, False])
async def test_manager_decision_remains_distinct_from_workflow_rejection(
    database: PostgresDatabase, accepted: bool
) -> None:
    evidence = (
        decision()
        if accepted
        else encode_safe_value(
            {
                "managerRequestDecisionId": "decision-1",
                "managerRejection": {"sourceResult": "manager-rejected"},
            }
        )
    )
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await repo.open_manager_receipt(
            SCOPE,
            "Manager",
            "manager-1",
            "manager-key",
            "execution",
            encode_safe_value({"commandId": "manager-1"}),
            None,
        )
        assert (
            await repo.commit_manager_decision(
                SCOPE, "Manager", "manager-1", 0, 0, accepted, evidence
            )
        ).outcome == "Committed"
        recovered = await repo.load_recovery(SCOPE, "Manager", "manager-1")
        assert recovered.value is not None
        assert recovered.value["state"] == (
            "AcceptedCommandPending" if accepted else "ManagerRejected"
        )
        assert recovered.value["decision_evidence"] == evidence
        assert recovered.value["start_command_id"] is None
        assert await session.scalar(text("SELECT count(*) FROM employee_observations")) == 0
        if accepted:
            original = start()
            invalid = replace(original, payload={**original.payload, "invalid": True})
            with pytest.raises(UnsafeM7CommandValue):
                await repo.commit_manager_decision_and_pending_command(
                    SCOPE, "Manager", "manager-1", 1, 0, evidence, invalid
                )
            assert (
                await repo.load_recovery(SCOPE, "Manager", "manager-1")
            ).value == recovered.value


def make_source_receipt(tag: str, source: ResultEnvelope) -> WorkflowCommandReceipt:
    error = (
        None
        if tag == "Acknowledged"
        else ErrorEnvelope(
            "error-1",
            "WORKFLOW_AI_AUTHORIZATION_REVOKED"
            if tag == "TerminalWithWorkflow"
            else "WORKFLOW_DEFINITION_INVALID",
            ErrorCategory.VALIDATION,
            ErrorSeverity.WARNING,
            RetryClassification.NEVER_RETRY,
            "source rejection",
            "Workflow Engine",
            source.subject_reference,
            SCOPE.tenant_id,
            SCOPE.workspace_id,
            "correlation",
            "start-1",
            datetime(2026, 9, 9, tzinfo=UTC),
        )
    )
    return WorkflowCommandReceipt(
        start(),
        source,
        "workflow-1" if tag in {"Acknowledged", "TerminalWithWorkflow"} else "start-1",
        error,
        CommandProcessingState.COMPLETED,
    )


async def test_source_registration_cannot_reintroduce_command_digest_authority(
    database: PostgresDatabase,
) -> None:
    from aieos.adapters.persistence_postgres.employee import Digest

    first = start()
    changed = replace(first, metadata=replace(first.metadata, trace_id="changed"))
    fingerprint = Digest("StartWorkflowCommand", "1.0", "a" * 64)
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        assert (
            await repo.register_source(
                SCOPE,
                "Workflow Engine",
                "1.0",
                "StartWorkflowCommand",
                "start-1",
                fingerprint,
                encode_start_workflow_evidence(first).decode("utf-8"),
            )
        ).outcome == "Registered"
        assert (
            await repo.register_source(
                SCOPE,
                "Workflow Engine",
                "1.0",
                "StartWorkflowCommand",
                "start-1",
                replace(fingerprint, value="b" * 64),
                encode_start_workflow_evidence(first).decode("utf-8"),
            )
        ).outcome == "IdenticalSource"
        assert (
            await repo.register_source(
                SCOPE,
                "Workflow Engine",
                "1.0",
                "StartWorkflowCommand",
                "start-1",
                fingerprint,
                encode_start_workflow_evidence(changed).decode("utf-8"),
            )
        ).outcome == "SourceConflict"


async def test_competing_command_under_same_source_result_quarantines_both(
    database: PostgresDatabase,
) -> None:
    item = response("Acknowledged")
    second = replace(start("start-2", "second-key"), causation_id="decision-2")
    second_decision = encode_safe_value(
        {
            "managerRequestDecisionId": "decision-2",
            "requestId": "request",
            "selectedWorkflowDefinitionVersionId": "version",
            "selectionEvidence": {"source": "Manager"},
        }
    )
    result = replace(
        TypeAdapter(ResultEnvelope).validate_json(item.source_result),
        command_id="start-2",
        causation_id="start-2",
        subject_reference="workflow-2",
        value_reference="workflow-2",
    )
    source_receipt = WorkflowCommandReceipt(
        second, result, "workflow-2", state=CommandProcessingState.COMPLETED
    )
    source_bytes = TypeAdapter(WorkflowCommandReceipt).dump_json(source_receipt)
    proof = encode_safe_value(
        {
            "tenantId": SCOPE.tenant_id,
            "workspaceId": SCOPE.workspace_id,
            "startWorkflowCommandId": "start-2",
            "sourceResultId": "result-1",
            "workflowId": "workflow-2",
            "sourceEvidence": source_bytes.decode("utf-8"),
        }
    )
    competing = replace(
        item,
        source_result=TypeAdapter(ResultEnvelope).dump_json(result),
        workflow_id="workflow-2",
        workflow_identity_proof=proof,
    )
    async with database.transaction() as session:
        repo = PostgresEmployeePersistence(session)
        await admit(repo)
        await stage_source(session, item)
        assert (await repo.capture_response(SCOPE, "start-1", item)).outcome == "ResponseCaptured"
        await repo.open_manager_receipt(
            SCOPE,
            "Manager",
            "manager-2",
            "manager-key-2",
            "execution-2",
            encode_safe_value({"commandId": "manager-2"}),
            None,
        )
        assert (
            await repo.commit_manager_decision_and_pending_command(
                SCOPE, "Manager", "manager-2", 0, 0, second_decision, second
            )
        ).outcome == "Created"
        await session.execute(
            text("""INSERT INTO command_idempotency
            (tenant_id,workspace_id,target_component,idempotency_key,command_id,command_hash,completed,payload)
            VALUES (:t,:w,'Workflow Engine','second-key','start-2','hint',true,:p)"""),
            {"t": SCOPE.tenant_id, "w": SCOPE.workspace_id, "p": source_bytes},
        )
        instance = WorkflowInstance(
            "workflow-2",
            "step-2",
            WorkflowDefinition("definition", "version", "skill"),
            SCOPE.tenant_id,
            SCOPE.workspace_id,
            "request",
            "correlation",
            second.metadata.authorization,
            second.payload,
            1.0,
        )
        await session.execute(
            text("""INSERT INTO workflows
            (tenant_id,workspace_id,workflow_id,state,version,correlation_id,payload)
            VALUES (:t,:w,'workflow-2','Running',1,'correlation',:p)"""),
            {
                "t": SCOPE.tenant_id,
                "w": SCOPE.workspace_id,
                "p": TypeAdapter(WorkflowInstance).dump_json(instance),
            },
        )
        assert (
            await repo.capture_response(SCOPE, "start-2", competing)
        ).outcome == "QuarantinedConflict"
        assert (await repo.read_response(SCOPE, "start-1")).outcome == "QuarantinedConflict"
        recovered = await repo.load_recovery(SCOPE, "Manager", "manager-2")
        assert recovered.value is not None and recovered.value["dispatch_allowed"] is False
        assert recovered.value["recovery_blocked"] is not None
