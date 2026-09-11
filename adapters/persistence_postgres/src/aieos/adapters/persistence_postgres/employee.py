"""M7-C durable persistence ports and PostgreSQL implementation.

This module deliberately contains no catalog, admission, or Manager decisions.  It
only preserves the immutable facts and fenced transitions supplied by M7-D/E.
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, timedelta
from hashlib import sha256
from typing import cast

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aieos.contracts.commands.models import CommandEnvelope
from aieos.contracts.results import ResultEnvelope
from aieos.workflow_engine.engine import WorkflowCommandReceipt, WorkflowInstance

from .employee_codec import (
    EVIDENCE_PROFILE,
    REPLAY_PATH,
    UnsafeM7CommandValue,
    decode_safe_value,
    encode_safe_value,
    encode_start_workflow_evidence,
    identifier,
    reconstruct_start_workflow,
)
from .employee_references import (
    DurableReference,
    ReferenceOwner,
    encode_durable_reference,
    reconstruct_durable_reference,
)
from .employee_response import WorkflowResponse, reconstruct_response

PROFILE = "AIEOS-M7-CD-v1"
RESPONSE_TAGS = frozenset(
    {
        "ManagerRejected",
        "AcceptedCommandPending",
        "DispatchUnconfirmed",
        "RejectedNoWorkflow",
        "TerminalWithWorkflow",
        "Acknowledged",
        "TerminalCapturedIdentityUnresolved",
    }
)


@dataclass(frozen=True)
class Digest:
    domain: str
    contract_version: str
    value: str
    algorithm: str = "sha256"


def digest(domain: str, contract_version: str, canonical_bytes: bytes) -> Digest:
    """Hash the CD-v1 typed frame; the semantic value is already canonicalized."""
    frame = encode_safe_value([PROFILE, domain, contract_version, canonical_bytes.decode("utf-8")])
    return Digest(domain, contract_version, sha256(frame).hexdigest())


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    workspace_id: str


@dataclass(frozen=True)
class OperationResult:
    outcome: str
    value: dict[str, object] | None = None


class PostgresEmployeePersistence:
    """C-owned SCB A-L storage operations.

    The generic immutable-fact primitive backs observations, source evidence,
    quarantine and administrative facts.  The database constraints remain the
    authoritative concurrency guard; callers must provide verified scope.
    """

    def __init__(
        self,
        session: AsyncSession,
        reference_owners: Mapping[tuple[str, str], ReferenceOwner] | None = None,
    ) -> None:
        self._session = session
        self._reference_owners = reference_owners or {}

    async def open_manager_receipt(
        self,
        scope: Scope,
        target: str,
        command_id: str,
        idempotency_key: str,
        execution_id: str,
        complete_command: bytes,
        fingerprint: str | None,
    ) -> OperationResult:
        identifier(scope.tenant_id)
        identifier(scope.workspace_id)
        identifier(target)
        identifier(command_id)
        identifier(idempotency_key, 256)
        identifier(execution_id)
        basis = decode_safe_value(complete_command)
        if type(basis) is not dict or not basis:
            raise UnsafeM7CommandValue("missing complete Manager command basis")
        row = (
            await self._session.execute(
                text("""INSERT INTO employee_manager_receipts
          (tenant_id,workspace_id,manager_target,manager_command_id,idempotency_key,employee_execution_id,complete_command,command_profile,command_fingerprint,state)
          VALUES (:t,:w,:m,:c,:k,:e,:x,:p,:f,'DecisionPending') ON CONFLICT DO NOTHING RETURNING complete_command"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "m": target,
                    "c": command_id,
                    "k": idempotency_key,
                    "e": execution_id,
                    "x": complete_command,
                    "p": EVIDENCE_PROFILE,
                    "f": fingerprint,
                },
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Opened")
        rows = (
            (
                await self._session.execute(
                    text("""SELECT * FROM employee_manager_receipts
            WHERE tenant_id=:t AND workspace_id=:w AND manager_target=:m
            AND (manager_command_id=:c OR idempotency_key=:k) FOR UPDATE"""),
                    {
                        "t": scope.tenant_id,
                        "w": scope.workspace_id,
                        "m": target,
                        "c": command_id,
                        "k": idempotency_key,
                    },
                )
            )
            .mappings()
            .all()
        )
        same = (
            len(rows) == 1
            and rows[0]["manager_command_id"] == command_id
            and rows[0]["idempotency_key"] == idempotency_key
            and rows[0]["employee_execution_id"] == execution_id
            and rows[0]["complete_command"] == complete_command
            and rows[0]["command_profile"] == EVIDENCE_PROFILE
        )
        return OperationResult(
            "Existing" if same else "IdentityConflict", dict(rows[0]) if same else None
        )

    async def append_observation(
        self,
        scope: Scope,
        kind: str,
        source_identity: str,
        evidence: bytes,
        fingerprint: str | None = None,
    ) -> OperationResult:
        created = (
            await self._session.execute(
                text("""INSERT INTO employee_observations
          (tenant_id,workspace_id,observation_kind,source_identity,evidence,fingerprint)
          VALUES (:t,:w,:k,:i,:e,:f) ON CONFLICT DO NOTHING RETURNING evidence"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "k": kind,
                    "i": source_identity,
                    "e": evidence,
                    "f": fingerprint,
                },
            )
        ).scalar_one_or_none()
        if created is not None:
            return OperationResult("Appended")
        old = (
            await self._session.execute(
                text(
                    "SELECT evidence FROM employee_observations WHERE tenant_id=:t AND workspace_id=:w AND observation_kind=:k AND source_identity=:i"
                ),
                {"t": scope.tenant_id, "w": scope.workspace_id, "k": kind, "i": source_identity},
            )
        ).scalar_one()
        if old == evidence:
            quarantined = (
                await self._session.execute(
                    text("""SELECT quarantined FROM employee_observations
                WHERE tenant_id=:t AND workspace_id=:w AND observation_kind=:k AND source_identity=:i"""),
                    {
                        "t": scope.tenant_id,
                        "w": scope.workspace_id,
                        "k": kind,
                        "i": source_identity,
                    },
                )
            ).scalar_one()
            return OperationResult("QuarantinedConflict" if quarantined else "Existing")
        await self._session.execute(
            text("""INSERT INTO employee_observation_conflicts
            (tenant_id,workspace_id,source_identity,existing_evidence,incoming_evidence)
            VALUES (:t,:w,:i,:old,:new)"""),
            {
                "t": scope.tenant_id,
                "w": scope.workspace_id,
                "i": source_identity,
                "old": old,
                "new": evidence,
            },
        )
        await self._session.execute(
            text(
                "UPDATE employee_observations SET quarantined=true WHERE tenant_id=:t AND workspace_id=:w AND observation_kind=:k AND source_identity=:i"
            ),
            {"t": scope.tenant_id, "w": scope.workspace_id, "k": kind, "i": source_identity},
        )
        return OperationResult("QuarantinedConflict")

    async def transition_receipt(
        self,
        scope: Scope,
        target: str,
        command_id: str,
        expected_revision: int,
        expected_fence: int,
        state: str,
    ) -> OperationResult:
        """CAS/fence delivery transition; source decision/response methods own other phases."""
        if state != "CommandCreatedDispatchPending":
            raise UnsafeM7CommandValue("phase requires its authoritative evidence operation")
        row = (
            (
                await self._session.execute(
                    text("""UPDATE employee_manager_receipts
          SET state=:s, revision=revision+1 WHERE tenant_id=:t AND workspace_id=:w
          AND manager_target=:m AND manager_command_id=:c AND revision=:r AND fence=:f
          AND recovery_blocked IS NULL AND state NOT IN ('WorkflowStartAcknowledged','WorkflowStartRejectedNoWorkflow','WorkflowStartTerminalWithWorkflow','SourceTerminalCapturedClassificationPending')
          RETURNING revision,fence,state"""),
                    {
                        "t": scope.tenant_id,
                        "w": scope.workspace_id,
                        "m": target,
                        "c": command_id,
                        "r": expected_revision,
                        "f": expected_fence,
                        "s": state,
                    },
                )
            )
            .mappings()
            .first()
        )
        return OperationResult("Transitioned" if row else "FenceLost", dict(row) if row else None)

    async def load_recovery(self, scope: Scope, target: str, command_id: str) -> OperationResult:
        """Load original command evidence/path; this never regenerates a command."""
        row = (
            (
                await self._session.execute(
                    text("""SELECT r.*, c.complete_evidence,
            c.replay_path,c.command_profile AS start_profile,c.idempotency_key AS start_key,c.reference_evidence
            FROM employee_manager_receipts r LEFT JOIN employee_start_workflow_commands c
            ON c.tenant_id=r.tenant_id AND c.workspace_id=r.workspace_id AND c.command_id=r.start_command_id
            WHERE r.tenant_id=:t AND r.workspace_id=:w AND r.manager_target=:m AND r.manager_command_id=:c"""),
                    {"t": scope.tenant_id, "w": scope.workspace_id, "m": target, "c": command_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return OperationResult("NotFound")
        if row["command_profile"] != EVIDENCE_PROFILE:
            return OperationResult("UnsupportedVersion")
        try:
            manager_basis = decode_safe_value(bytes(row["complete_command"]))
            if type(manager_basis) is not dict or not manager_basis:
                return OperationResult("IntegrityConflict")
        except ValueError:
            return OperationResult("IntegrityConflict")
        value = dict(row)
        if row["start_command_id"] is not None:
            if row["complete_evidence"] is None:
                return OperationResult("DependencyUnavailable")
            if row["start_profile"] != EVIDENCE_PROFILE or row["replay_path"] != REPLAY_PATH:
                return OperationResult("UnsupportedVersion")
            try:
                command = reconstruct_start_workflow(bytes(row["complete_evidence"]))
                self._validate_binding(scope, command, row["start_command_id"], row["start_key"])
            except UnsafeM7CommandValue:
                return OperationResult("IntegrityConflict")
            if row["reference_evidence"] is not None:
                try:
                    bindings = cast(
                        dict[str, object], decode_safe_value(bytes(row["reference_evidence"]))
                    )
                    for position, encoded_reference in bindings.items():
                        reference = reconstruct_durable_reference(encoded_reference, scope)
                        owner = self._reference_owners.get(
                            (reference.owning_contract, reference.contract_version)
                        )
                        if owner is None:
                            return OperationResult("DependencyUnavailable")
                        if encode_safe_value(
                            command.payload.get(position)
                        ) != encode_durable_reference(reference):
                            return OperationResult("IntegrityConflict")
                        await self.revalidate_durable_reference(
                            scope, reference, reference.allowed_purpose, owner
                        )
                except ValueError:
                    return OperationResult("IntegrityConflict")
            value["dispatch_allowed"] = (
                row["state"] == "CommandCreatedDispatchPending" and row["recovery_blocked"] is None
            )
            value["start_command"] = command
        return OperationResult("Recovered", value)

    async def commit_checkpoint(
        self,
        scope: Scope,
        kind: str,
        execution_id: str,
        expected_revision: int,
        expected_fence: int,
        labelled_view: bytes,
        completeness: str,
    ) -> OperationResult:
        row = (
            (
                await self._session.execute(
                    text("""UPDATE employee_projection_checkpoints
          SET revision=revision+1,labelled_view=:v,completeness=:c WHERE tenant_id=:t AND workspace_id=:w
          AND projection_kind=:k AND employee_execution_id=:e AND revision=:r AND fence=:f RETURNING revision,fence"""),
                    {
                        "t": scope.tenant_id,
                        "w": scope.workspace_id,
                        "k": kind,
                        "e": execution_id,
                        "r": expected_revision,
                        "f": expected_fence,
                        "v": labelled_view,
                        "c": completeness,
                    },
                )
            )
            .mappings()
            .first()
        )
        return OperationResult("Committed" if row else "FenceLost", dict(row) if row else None)

    async def resolve_conflict(
        self, scope: Scope, conflict_id: str, evidence: bytes, authoritative_source_identity: str
    ) -> OperationResult:
        conflict = (
            await self._session.execute(
                text("""SELECT source_identity FROM employee_observation_conflicts
            WHERE tenant_id=:t AND workspace_id=:w AND CAST(id AS text)=:c"""),
                {"t": scope.tenant_id, "w": scope.workspace_id, "c": conflict_id},
            )
        ).scalar_one_or_none()
        if conflict is None:
            return OperationResult("NotFound")
        row = (
            await self._session.execute(
                text("""INSERT INTO employee_conflict_resolutions
          (tenant_id,workspace_id,conflict_id,resolution_evidence,authoritative_source_identity)
          VALUES (:t,:w,:c,:e,:s) ON CONFLICT DO NOTHING RETURNING conflict_id"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "c": conflict_id,
                    "e": evidence,
                    "s": authoritative_source_identity,
                },
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Resolved")
        old = (
            await self._session.execute(
                text("""SELECT resolution_evidence,authoritative_source_identity FROM employee_conflict_resolutions
            WHERE tenant_id=:t AND workspace_id=:w AND conflict_id=:c"""),
                {"t": scope.tenant_id, "w": scope.workspace_id, "c": conflict_id},
            )
        ).one()
        return OperationResult(
            "ExistingResolution"
            if tuple(old) == (evidence, authoritative_source_identity)
            else "IntegrityConflict"
        )

    async def capture_response(
        self, scope: Scope, command_id: str, response: WorkflowResponse
    ) -> OperationResult:
        """Capture verified source union and close dispatch atomically; preserve disagreements."""
        source = response.validate(scope.tenant_id, scope.workspace_id, command_id)
        evidence = response.encode()
        source_row = (
            await self._session.execute(
                text("""SELECT payload FROM command_idempotency
            WHERE tenant_id=:t AND workspace_id=:w AND target_component='Workflow Engine'
            AND command_id=:c AND completed=true"""),
                {"t": scope.tenant_id, "w": scope.workspace_id, "c": command_id},
            )
        ).scalar_one_or_none()
        if source_row is None:
            return OperationResult("DependencyUnavailable")
        try:
            source_receipt = TypeAdapter(WorkflowCommandReceipt).validate_json(bytes(source_row))
            loaded = source_receipt.command
            if loaded.timestamp.utcoffset() != timedelta(0) or (
                loaded.metadata.expires_at is not None
                and loaded.metadata.expires_at.utcoffset() != timedelta(0)
            ):
                return OperationResult("IntegrityConflict")
            source_receipt.command = replace(
                loaded,
                timestamp=loaded.timestamp.replace(tzinfo=UTC),
                metadata=replace(
                    loaded.metadata,
                    expires_at=None
                    if loaded.metadata.expires_at is None
                    else loaded.metadata.expires_at.replace(tzinfo=UTC),
                ),
            )
            self._validate_binding(
                scope,
                source_receipt.command,
                command_id,
                source_receipt.command.metadata.idempotency_key,
            )
            if (
                TypeAdapter(ResultEnvelope).dump_json(source_receipt.result)
                != response.source_result
            ):
                return OperationResult("IntegrityConflict")
            if (
                response.workflow_id is not None
                and source_receipt.workflow_id != response.workflow_id
            ):
                return OperationResult("IntegrityConflict")
            if response.tag == "RejectedNoWorkflow":
                # Frozen _reject stores CommandId in its workflow_id field: this is a sentinel.
                sentinel = source_receipt.command.workflow_id or command_id
                error = source_receipt.error
                pre_instance_codes = {
                    "WORKFLOW_START_UNAUTHORIZED",
                    "AUTHORITATIVE_RESULT_ID_INVALID",
                    "WORKFLOW_DEFINITION_INVALID",
                    "WORKFLOW_REFERENCE_IDENTITY_INVALID",
                    "CLASSIFY_AND_ROUTE_INPUT_INVALID",
                    "WORKFLOW_AI_BUDGET_ENVELOPE_REQUIRED",
                    "WORKFLOW_AI_BUDGET_ENVELOPE_INVALID",
                    "WORKFLOW_AI_BUDGET_ENVELOPE_SCOPE_MISMATCH",
                    "WORKFLOW_COMMAND_VERSION_UNSUPPORTED",
                }
                if (
                    source_receipt.workflow_id != sentinel
                    or source.subject_reference != sentinel
                    or error is None
                    or error.error_id != source.error_id
                    or error.error_code not in pre_instance_codes
                ):
                    return OperationResult("IntegrityConflict")
            if response.workflow_id is not None:
                workflow_bytes = (
                    await self._session.execute(
                        text("""SELECT payload FROM workflows
                    WHERE tenant_id=:t AND workspace_id=:w AND workflow_id=:i"""),
                        {"t": scope.tenant_id, "w": scope.workspace_id, "i": response.workflow_id},
                    )
                ).scalar_one_or_none()
                if workflow_bytes is None:
                    return OperationResult("DependencyUnavailable")
                workflow = TypeAdapter(WorkflowInstance).validate_json(bytes(workflow_bytes))
                if (
                    workflow.tenant_id,
                    workflow.workspace_id,
                    workflow.workflow_id,
                    workflow.request_id,
                    workflow.correlation_id,
                ) != (
                    scope.tenant_id,
                    scope.workspace_id,
                    response.workflow_id,
                    source_receipt.command.metadata.request_id,
                    source_receipt.command.correlation_id,
                ) or source.subject_reference != response.workflow_id:
                    return OperationResult("IntegrityConflict")
                if (
                    response.tag == "Acknowledged"
                    and source.value_reference != response.workflow_id
                ):
                    return OperationResult("IntegrityConflict")
                if response.tag == "TerminalWithWorkflow" and (
                    source_receipt.error is None
                    or source_receipt.error.error_id != source.error_id
                    or source_receipt.error.error_code
                    not in {"WORKFLOW_AI_BUDGET_EXHAUSTED", "WORKFLOW_AI_AUTHORIZATION_REVOKED"}
                ):
                    return OperationResult("IntegrityConflict")
            if response.workflow_identity_proof is not None:
                proof = cast(dict[str, object], decode_safe_value(response.workflow_identity_proof))
                if proof["sourceEvidence"] != bytes(source_row).decode("utf-8"):
                    return OperationResult("IntegrityConflict")
        except ValueError:
            return OperationResult("IntegrityConflict")
        params = {"t": scope.tenant_id, "w": scope.workspace_id, "c": command_id}
        async with self._session.begin_nested():
            receipt = (
                (
                    await self._session.execute(
                        text("""SELECT * FROM employee_manager_receipts
                WHERE tenant_id=:t AND workspace_id=:w AND start_command_id=:c FOR UPDATE"""),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if receipt is None:
                return OperationResult("NotFound")
            retained = (
                await self._session.execute(
                    text("""SELECT complete_evidence FROM employee_start_workflow_commands
                WHERE tenant_id=:t AND workspace_id=:w AND command_id=:c"""),
                    params,
                )
            ).scalar_one_or_none()
            if retained is None or reconstruct_start_workflow(
                bytes(retained)
            ) != reconstruct_start_workflow(encode_start_workflow_evidence(source_receipt.command)):
                return OperationResult("IntegrityConflict")
            old = (
                (
                    await self._session.execute(
                        text("""SELECT * FROM employee_observations
                WHERE tenant_id=:t AND workspace_id=:w AND observation_kind='WorkflowStartResponse' AND source_identity=:c FOR UPDATE"""),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if old is not None:
                if (
                    old["evidence"] == evidence
                    and old["response_tag"] == response.tag
                    and old["workflow_id"] == response.workflow_id
                ):
                    return OperationResult(
                        "QuarantinedConflict" if old["quarantined"] else "ExistingResponse"
                    )
                conflict_id = (
                    await self._session.execute(
                        text("""INSERT INTO employee_observation_conflicts
                    (tenant_id,workspace_id,source_identity,existing_evidence,incoming_evidence) VALUES (:t,:w,:c,:old,:new) RETURNING id"""),
                        {**params, "old": old["evidence"], "new": evidence},
                    )
                ).scalar_one()
                await self._session.execute(
                    text("""UPDATE employee_observations SET quarantined=true
                    WHERE tenant_id=:t AND workspace_id=:w AND observation_kind='WorkflowStartResponse' AND source_identity=:c"""),
                    params,
                )
                await self._session.execute(
                    text("""UPDATE employee_manager_receipts SET recovery_blocked=:e
                    WHERE tenant_id=:t AND workspace_id=:w AND start_command_id=:c"""),
                    {**params, "e": evidence},
                )
                return OperationResult("QuarantinedConflict", {"conflict_id": str(conflict_id)})
            registration = await self.append_observation(
                scope, "WorkflowStartResult", source.result_id, evidence
            )
            if registration.outcome == "QuarantinedConflict":
                prior = (
                    await self._session.execute(
                        text("""SELECT evidence FROM employee_observations
                    WHERE tenant_id=:t AND workspace_id=:w AND observation_kind='WorkflowStartResult' AND source_identity=:i"""),
                        {"t": scope.tenant_id, "w": scope.workspace_id, "i": source.result_id},
                    )
                ).scalar_one()
                prior_response = reconstruct_response(bytes(prior))
                prior_command = (
                    TypeAdapter(ResultEnvelope)
                    .validate_json(prior_response.source_result)
                    .command_id
                )
                await self._session.execute(
                    text("""UPDATE employee_observations SET quarantined=true
                    WHERE tenant_id=:t AND workspace_id=:w AND observation_kind='WorkflowStartResponse' AND source_identity=:c"""),
                    {"t": scope.tenant_id, "w": scope.workspace_id, "c": prior_command},
                )
                await self._session.execute(
                    text("""UPDATE employee_manager_receipts SET recovery_blocked=:e
                    WHERE tenant_id=:t AND workspace_id=:w AND start_command_id IN (:c,:prior)"""),
                    {**params, "prior": prior_command, "e": evidence},
                )
                return OperationResult("QuarantinedConflict")
            await self._session.execute(
                text("""INSERT INTO employee_observations
                (tenant_id,workspace_id,observation_kind,source_identity,evidence,response_tag,workflow_id,employee_execution_id)
                VALUES (:t,:w,'WorkflowStartResponse',:c,:e,:tag,:wid,:eid)"""),
                {
                    **params,
                    "e": evidence,
                    "tag": response.tag,
                    "wid": response.workflow_id,
                    "eid": receipt["employee_execution_id"],
                },
            )
            states = {
                "Acknowledged": "WorkflowStartAcknowledged",
                "RejectedNoWorkflow": "WorkflowStartRejectedNoWorkflow",
                "TerminalWithWorkflow": "WorkflowStartTerminalWithWorkflow",
                "TerminalCapturedIdentityUnresolved": "SourceTerminalCapturedClassificationPending",
            }
            await self._session.execute(
                text("""UPDATE employee_manager_receipts SET state=:s,observation_progress='Pending',revision=revision+1
                WHERE tenant_id=:t AND workspace_id=:w AND start_command_id=:c"""),
                {**params, "s": states[response.tag]},
            )
        return OperationResult("ResponseCaptured", {"source_result_id": source.result_id})

    async def read_response(self, scope: Scope, command_id: str) -> OperationResult:
        row = (
            (
                await self._session.execute(
                    text("""SELECT * FROM employee_observations
            WHERE tenant_id=:t AND workspace_id=:w AND observation_kind='WorkflowStartResponse' AND source_identity=:c"""),
                    {"t": scope.tenant_id, "w": scope.workspace_id, "c": command_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return OperationResult("NotFound")
        if row["quarantined"]:
            return OperationResult("QuarantinedConflict")
        try:
            response = reconstruct_response(bytes(row["evidence"]))
            response.validate(scope.tenant_id, scope.workspace_id, command_id)
            if (response.tag, response.workflow_id) != (row["response_tag"], row["workflow_id"]):
                return OperationResult("IntegrityConflict")
        except ValueError:
            return OperationResult("IntegrityConflict")
        return OperationResult("Found", {"response": response})

    async def confirm_employee_observation(
        self, scope: Scope, command_id: str, evidence: bytes
    ) -> OperationResult:
        row = (
            await self._session.execute(
                text("""UPDATE employee_manager_receipts r
            SET observation_progress='Recorded' WHERE r.tenant_id=:t AND r.workspace_id=:w AND r.start_command_id=:c
            AND EXISTS (SELECT 1 FROM employee_observations o WHERE o.tenant_id=r.tenant_id AND o.workspace_id=r.workspace_id
            AND o.source_identity=r.start_command_id AND o.observation_kind='WorkflowStartResponse' AND NOT o.quarantined AND o.evidence=:e)
            RETURNING manager_command_id"""),
                {"t": scope.tenant_id, "w": scope.workspace_id, "c": command_id, "e": evidence},
            )
        ).scalar_one_or_none()
        return OperationResult("Confirmed" if row is not None else "IntegrityConflict")

    async def advance_administrative_head(
        self,
        scope: Scope,
        target_type: str,
        target_version_id: str,
        expected_revision: int,
        evidence: bytes,
        disposition: str,
    ) -> OperationResult:
        row = (
            await self._session.execute(
                text("""UPDATE employee_administrative_heads SET revision=revision+1,
          decision_evidence=:e,disposition=:d WHERE tenant_id=:t AND workspace_id=:w AND target_type=:k
          AND target_version_id=:v AND revision=:r RETURNING revision"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "k": target_type,
                    "v": target_version_id,
                    "r": expected_revision,
                    "e": evidence,
                    "d": disposition,
                },
            )
        ).scalar_one_or_none()
        return OperationResult("Advanced" if row is not None else "StaleRevision")

    async def save_start_workflow_command(
        self,
        scope: Scope,
        command_id: str,
        idempotency_key: str,
        complete_evidence: bytes,
        fingerprint: str | None,
        replay_path: str = "FrozenM6PostgresWorkflowHost52271c4",
    ) -> OperationResult:
        """Validate before SQL and converge only on both exact bindings and full basis."""
        command = reconstruct_start_workflow(complete_evidence)
        self._validate_binding(scope, command, command_id, idempotency_key)
        if replay_path != REPLAY_PATH:
            return OperationResult("UnsupportedVersion")
        row = (
            await self._session.execute(
                text("""INSERT INTO employee_start_workflow_commands
            (tenant_id,workspace_id,command_id,idempotency_key,complete_evidence,fingerprint,replay_path,command_profile)
            VALUES (:t,:w,:c,:k,:e,:f,:p,:v) ON CONFLICT DO NOTHING RETURNING command_id"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "c": command_id,
                    "k": idempotency_key,
                    "e": complete_evidence,
                    "f": fingerprint,
                    "p": replay_path,
                    "v": EVIDENCE_PROFILE,
                },
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Created")
        rows = (
            (
                await self._session.execute(
                    text("""SELECT * FROM employee_start_workflow_commands
            WHERE tenant_id=:t AND workspace_id=:w AND (command_id=:c OR idempotency_key=:k) FOR UPDATE"""),
                    {
                        "t": scope.tenant_id,
                        "w": scope.workspace_id,
                        "c": command_id,
                        "k": idempotency_key,
                    },
                )
            )
            .mappings()
            .all()
        )
        same = (
            len(rows) == 1
            and rows[0]["command_id"] == command_id
            and rows[0]["idempotency_key"] == idempotency_key
            and rows[0]["complete_evidence"] == complete_evidence
            and rows[0]["command_profile"] == EVIDENCE_PROFILE
            and rows[0]["replay_path"] == replay_path
        )
        return OperationResult("Existing" if same else "IdentityConflict")

    @staticmethod
    def _validate_binding(
        scope: Scope, command: CommandEnvelope, command_id: str, key: str
    ) -> None:
        if (
            command.tenant_id,
            command.workspace_id,
            command.command_id,
            command.metadata.idempotency_key,
        ) != (scope.tenant_id, scope.workspace_id, command_id, key):
            raise UnsafeM7CommandValue("command evidence identity/scope mismatch")

    async def commit_admission(
        self,
        scope: Scope,
        principal: str,
        key: str,
        caller_evidence: bytes,
        execution_id: str,
        snapshot_id: str,
        command_id: str,
        payload: str,
        fingerprint: str | None = None,
    ) -> OperationResult:
        """Atomically bind an idempotency key to exact caller evidence, never its digest."""
        row = (
            (
                await self._session.execute(
                    text("""
          INSERT INTO employee_admissions (tenant_id,workspace_id,principal_id,idempotency_key,caller_evidence,caller_profile,caller_fingerprint,execution_id,snapshot_id,manager_command_id,payload)
          VALUES (:t,:w,:p,:k,:b,:q,:f,:e,:s,:c,:v)
          ON CONFLICT DO NOTHING
          RETURNING caller_evidence,execution_id,snapshot_id,manager_command_id"""),
                    dict(
                        t=scope.tenant_id,
                        w=scope.workspace_id,
                        p=principal,
                        k=key,
                        b=caller_evidence,
                        q=PROFILE,
                        f=fingerprint,
                        e=execution_id,
                        s=snapshot_id,
                        c=command_id,
                        v=payload,
                    ),
                )
            )
            .mappings()
            .first()
        )
        if row:
            return OperationResult("Created", dict(row))
        old = (
            (
                await self._session.execute(
                    text(
                        "SELECT caller_evidence,execution_id,snapshot_id,manager_command_id FROM employee_admissions WHERE tenant_id=:t AND workspace_id=:w AND principal_id=:p AND idempotency_key=:k"
                    ),
                    dict(t=scope.tenant_id, w=scope.workspace_id, p=principal, k=key),
                )
            )
            .mappings()
            .first()
        )
        if old is None:
            return OperationResult("InputConflict")
        return OperationResult(
            "Existing" if old["caller_evidence"] == caller_evidence else "InputConflict", dict(old)
        )

    async def register_source(
        self,
        scope: Scope,
        component: str,
        version: str,
        kind: str,
        source_id: str,
        source: Digest,
        payload: str,
    ) -> OperationResult:
        command_kinds = {"StartWorkflowCommand", "EvaluateEmployeeRequestV2Command"}
        is_command = kind in command_kinds or source.domain in command_kinds
        profile = EVIDENCE_PROFILE if is_command else PROFILE
        if is_command:
            decode_safe_value(payload.encode("utf-8"))
            if kind == "StartWorkflowCommand" or source.domain == "StartWorkflowCommand":
                command = reconstruct_start_workflow(payload.encode("utf-8"))
                self._validate_binding(scope, command, source_id, command.metadata.idempotency_key)
        row = (
            await self._session.execute(
                text("""
          INSERT INTO employee_source_evidence (tenant_id,workspace_id,source_component,source_contract_version,source_kind,source_id,digest,profile,domain,payload)
          VALUES (:t,:w,:c,:v,:k,:i,:d,:p,:n,:x)
          ON CONFLICT DO NOTHING RETURNING digest"""),
                dict(
                    t=scope.tenant_id,
                    w=scope.workspace_id,
                    c=component,
                    v=version,
                    k=kind,
                    i=source_id,
                    d=source.value,
                    p=profile,
                    n=source.domain,
                    x=payload,
                ),
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Registered")
        old = (
            (
                await self._session.execute(
                    text(
                        "SELECT digest,payload,profile,domain FROM employee_source_evidence WHERE tenant_id=:t AND workspace_id=:w AND source_component=:c AND source_contract_version=:v AND source_kind=:k AND source_id=:i"
                    ),
                    dict(
                        t=scope.tenant_id,
                        w=scope.workspace_id,
                        c=component,
                        v=version,
                        k=kind,
                        i=source_id,
                    ),
                )
            )
            .mappings()
            .one()
        )
        if (
            old["profile"] == profile
            and old["domain"] == source.domain
            and (old["payload"] == payload if is_command else old["digest"] == source.value)
        ):
            return OperationResult("IdenticalSource")
        await self._session.execute(
            text(
                "INSERT INTO employee_lineage_conflicts (tenant_id,workspace_id,source_component,source_id,existing_digest,incoming_digest,state) VALUES (:t,:w,:c,:i,:e,:n,'Unresolved') ON CONFLICT DO NOTHING"
            ),
            dict(
                t=scope.tenant_id,
                w=scope.workspace_id,
                c=component,
                i=source_id,
                e=old["digest"],
                n=source.value,
            ),
        )
        if is_command:
            await self._session.execute(
                text("""INSERT INTO employee_observation_conflicts
                (tenant_id,workspace_id,source_identity,existing_evidence,incoming_evidence)
                VALUES (:t,:w,:i,:old,:new)"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "i": source_id,
                    "old": str(old["payload"]).encode("utf-8"),
                    "new": payload.encode("utf-8"),
                },
            )
        return OperationResult("SourceConflict")

    async def claim_handoff(
        self, scope: Scope, intent_id: str, owner: str, expected_revision: int, lease_seconds: int
    ) -> OperationResult:
        row = (
            (
                await self._session.execute(
                    text("""UPDATE employee_manager_handoffs SET revision=revision+1,fence=fence+1,lease_owner=:o,lease_expires_at=now() + (:s * interval '1 second')
          WHERE tenant_id=:t AND workspace_id=:w AND intent_id=:i AND revision=:r AND (lease_expires_at IS NULL OR lease_expires_at < now()) RETURNING revision,fence"""),
                    dict(
                        t=scope.tenant_id,
                        w=scope.workspace_id,
                        i=intent_id,
                        o=owner,
                        r=expected_revision,
                        s=lease_seconds,
                    ),
                )
            )
            .mappings()
            .first()
        )
        return OperationResult("Claimed" if row else "FenceLost", dict(row) if row else None)

    async def commit_durable_reference(
        self, scope: Scope, reference: DurableReference, owner: ReferenceOwner
    ) -> OperationResult:
        evidence = encode_durable_reference(reference)
        reconstruct_durable_reference(decode_safe_value(evidence), scope)
        current = await owner.validate_use(reference, reference.allowed_purpose)
        if current != reference.owner_evidence:
            return OperationResult("IntegrityConflict")
        params = {
            "t": scope.tenant_id,
            "w": scope.workspace_id,
            "o": reference.owning_contract,
            "k": reference.kind,
            "i": reference.identity,
            "p": reference.immutable_pin,
            "e": evidence,
        }
        created = (
            await self._session.execute(
                text("""INSERT INTO employee_durable_references
            (tenant_id,workspace_id,owning_contract,kind,identity,immutable_pin,evidence)
            VALUES (:t,:w,:o,:k,:i,:p,:e) ON CONFLICT DO NOTHING RETURNING evidence"""),
                params,
            )
        ).scalar_one_or_none()
        if created is not None:
            return OperationResult("Created")
        old = (
            await self._session.execute(
                text("""SELECT evidence FROM employee_durable_references
            WHERE tenant_id=:t AND workspace_id=:w AND owning_contract=:o AND kind=:k AND identity=:i AND immutable_pin=:p"""),
                params,
            )
        ).scalar_one()
        return OperationResult("Existing" if old == evidence else "IntegrityConflict")

    async def revalidate_durable_reference(
        self, scope: Scope, reference: DurableReference, purpose: str, owner: ReferenceOwner
    ) -> DurableReference:
        evidence = encode_durable_reference(reference)
        reconstruct_durable_reference(decode_safe_value(evidence), scope)
        if purpose != reference.allowed_purpose:
            raise UnsafeM7CommandValue("reference purpose mismatch")
        old = (
            await self._session.execute(
                text("""SELECT evidence FROM employee_durable_references
            WHERE tenant_id=:t AND workspace_id=:w AND owning_contract=:o AND kind=:k AND identity=:i AND immutable_pin=:p"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "o": reference.owning_contract,
                    "k": reference.kind,
                    "i": reference.identity,
                    "p": reference.immutable_pin,
                },
            )
        ).scalar_one_or_none()
        if old is None or old != evidence:
            raise UnsafeM7CommandValue("missing or changed authoritative reference evidence")
        restored = reconstruct_durable_reference(decode_safe_value(bytes(old)), scope)
        if await owner.validate_use(restored, purpose) != restored.owner_evidence:
            raise UnsafeM7CommandValue("reference owner evidence changed")
        return restored

    async def commit_manager_decision_and_pending_command(
        self,
        scope: Scope,
        target: str,
        manager_command_id: str,
        expected_revision: int,
        expected_fence: int,
        decision_evidence: bytes,
        command: CommandEnvelope,
        references: Mapping[str, DurableReference] | None = None,
    ) -> OperationResult:
        """SCB E: accepted source decision and its sole pending command in one savepoint.

        D supplies the verified Manager-owned decision. C does not select workflows.
        The enclosing C database transaction owns commit; no partial writes escape failure.
        """
        evidence = encode_start_workflow_evidence(command)
        decision = decode_safe_value(decision_evidence)
        if type(decision) is not dict:
            raise UnsafeM7CommandValue("invalid accepted Manager decision")
        fields = cast(dict[str, object], decision)
        if set(fields) != {
            "managerRequestDecisionId",
            "requestId",
            "selectedWorkflowDefinitionVersionId",
            "selectionEvidence",
        }:
            raise UnsafeM7CommandValue("incomplete accepted Manager decision")
        identifier(fields["managerRequestDecisionId"])
        if (
            fields["requestId"] != command.metadata.request_id
            or fields["selectedWorkflowDefinitionVersionId"]
            != command.payload.get("workflow_definition_version_id")
            or command.causation_id != fields["managerRequestDecisionId"]
            or not fields["selectionEvidence"]
        ):
            raise UnsafeM7CommandValue("decision/command association mismatch")
        params = {
            "t": scope.tenant_id,
            "w": scope.workspace_id,
            "m": target,
            "c": manager_command_id,
        }
        async with self._session.begin_nested() as transaction:
            row = (
                (
                    await self._session.execute(
                        text("""SELECT * FROM employee_manager_receipts
                WHERE tenant_id=:t AND workspace_id=:w AND manager_target=:m AND manager_command_id=:c FOR UPDATE"""),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return OperationResult("NotFound")
            if row["start_command_id"] is not None:
                if (
                    row["start_command_id"] != command.command_id
                    or row["decision_evidence"] != decision_evidence
                ):
                    return OperationResult("IdentityConflict")
                result = (
                    await self.save_start_workflow_with_references(scope, command, references)
                    if references is not None
                    else await self.save_start_workflow_command(
                        scope, command.command_id, command.metadata.idempotency_key, evidence, None
                    )
                )
                if result.outcome != "Existing":
                    await transaction.rollback()
                    return OperationResult("IntegrityConflict")
                return OperationResult("Existing", dict(row))
            if row["revision"] != expected_revision or row["fence"] != expected_fence:
                return OperationResult("FenceLost")
            if row["state"] not in {"DecisionPending", "AcceptedCommandPending"}:
                return OperationResult("IdentityConflict")
            if (
                row["decision_evidence"] is not None
                and row["decision_evidence"] != decision_evidence
            ):
                return OperationResult("IdentityConflict")
            result = (
                await self.save_start_workflow_with_references(scope, command, references)
                if references is not None
                else await self.save_start_workflow_command(
                    scope, command.command_id, command.metadata.idempotency_key, evidence, None
                )
            )
            if result.outcome != "Created":
                await transaction.rollback()
                return OperationResult("IdentityConflict")
            await self._session.execute(
                text("""UPDATE employee_manager_receipts SET start_command_id=:s,
                decision_evidence=:d,state='CommandCreatedDispatchPending',observation_progress='Pending',revision=revision+1
                WHERE tenant_id=:t AND workspace_id=:w AND manager_target=:m AND manager_command_id=:c"""),
                {**params, "s": command.command_id, "d": decision_evidence},
            )
        return OperationResult("Created")

    async def commit_admission_with_pending_command(
        self,
        scope: Scope,
        principal: str,
        key: str,
        caller_evidence: bytes,
        execution_id: str,
        snapshot_id: str,
        manager_command_id: str,
        snapshot: str,
        manager_target: str,
        manager_key: str,
        manager_evidence: bytes,
        decision_evidence: bytes,
        command: CommandEnvelope,
    ) -> OperationResult:
        """Compose C-owned writes atomically, with no external dispatch in the transaction."""
        encode_start_workflow_evidence(command)
        async with self._session.begin_nested() as transaction:
            admission = await self.commit_admission(
                scope,
                principal,
                key,
                caller_evidence,
                execution_id,
                snapshot_id,
                manager_command_id,
                snapshot,
            )
            if admission.outcome not in {"Created", "Existing"}:
                await transaction.rollback()
                return admission
            if (
                admission.value is None
                or admission.value["manager_command_id"] != manager_command_id
                or admission.value["execution_id"] != execution_id
                or admission.value["snapshot_id"] != snapshot_id
            ):
                await transaction.rollback()
                return OperationResult("IdentityConflict")
            receipt = await self.open_manager_receipt(
                scope,
                manager_target,
                manager_command_id,
                manager_key,
                execution_id,
                manager_evidence,
                None,
            )
            if receipt.outcome not in {"Opened", "Existing"}:
                await transaction.rollback()
                return receipt
            pending = await self.commit_manager_decision_and_pending_command(
                scope, manager_target, manager_command_id, 0, 0, decision_evidence, command
            )
            if pending.outcome not in {"Created", "Existing"}:
                await transaction.rollback()
                return pending
            await self._session.execute(
                text("""INSERT INTO employee_manager_handoffs
                (tenant_id,workspace_id,intent_id) VALUES (:t,:w,:i) ON CONFLICT DO NOTHING"""),
                {"t": scope.tenant_id, "w": scope.workspace_id, "i": manager_command_id},
            )
        return OperationResult(admission.outcome, admission.value)

    async def save_start_workflow_with_references(
        self,
        scope: Scope,
        command: CommandEnvelope,
        references: Mapping[str, DurableReference],
    ) -> OperationResult:
        """Retain owner-approved payload positions and reference evidence with the command.

        Positions are provided by the selected input owner; no new consumer is inferred.
        Recovery requires the same registered owners to revalidate each use.
        """
        evidence = encode_start_workflow_evidence(command)
        bindings: dict[str, object] = {}
        for position, reference in references.items():
            encoded = encode_durable_reference(reference)
            if (
                position not in command.payload
                or encode_safe_value(command.payload[position]) != encoded
            ):
                raise UnsafeM7CommandValue("reference does not match complete command position")
            bindings[position] = decode_safe_value(encoded)
        retained = encode_safe_value(bindings)
        async with self._session.begin_nested() as transaction:
            for reference in references.values():
                owner = self._reference_owners.get(
                    (reference.owning_contract, reference.contract_version)
                )
                if owner is None:
                    await transaction.rollback()
                    return OperationResult("DependencyUnavailable")
                result = await self.commit_durable_reference(scope, reference, owner)
                if result.outcome not in {"Created", "Existing"}:
                    await transaction.rollback()
                    return result
            result = await self.save_start_workflow_command(
                scope, command.command_id, command.metadata.idempotency_key, evidence, None
            )
            params = {
                "t": scope.tenant_id,
                "w": scope.workspace_id,
                "c": command.command_id,
                "r": retained,
            }
            if result.outcome == "Created":
                await self._session.execute(
                    text("""UPDATE employee_start_workflow_commands SET reference_evidence=:r
                    WHERE tenant_id=:t AND workspace_id=:w AND command_id=:c"""),
                    params,
                )
            elif result.outcome == "Existing":
                old = (
                    await self._session.execute(
                        text("""SELECT reference_evidence FROM employee_start_workflow_commands
                    WHERE tenant_id=:t AND workspace_id=:w AND command_id=:c"""),
                        params,
                    )
                ).scalar_one()
                if old != retained:
                    await transaction.rollback()
                    return OperationResult("IntegrityConflict")
            else:
                await transaction.rollback()
            return result

    async def commit_manager_decision(
        self,
        scope: Scope,
        target: str,
        command_id: str,
        expected_revision: int,
        expected_fence: int,
        accepted: bool,
        decision_evidence: bytes,
    ) -> OperationResult:
        """SCB E: retain Manager acceptance independently of StartWorkflow creation."""
        decision = decode_safe_value(decision_evidence)
        if type(decision) is not dict:
            raise UnsafeM7CommandValue("malformed Manager decision")
        fields = cast(dict[str, object], decision)
        required = (
            {
                "managerRequestDecisionId",
                "requestId",
                "selectedWorkflowDefinitionVersionId",
                "selectionEvidence",
            }
            if accepted
            else {"managerRequestDecisionId", "managerRejection"}
        )
        if set(fields) != required or any(value is None for value in fields.values()):
            raise UnsafeM7CommandValue("incomplete Manager decision")
        identifier(fields["managerRequestDecisionId"])
        params = {"t": scope.tenant_id, "w": scope.workspace_id, "m": target, "c": command_id}
        row = (
            (
                await self._session.execute(
                    text("""SELECT * FROM employee_manager_receipts
            WHERE tenant_id=:t AND workspace_id=:w AND manager_target=:m AND manager_command_id=:c FOR UPDATE"""),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return OperationResult("NotFound")
        if row["decision_evidence"] is not None:
            return OperationResult(
                "Existing" if row["decision_evidence"] == decision_evidence else "IdentityConflict"
            )
        if row["revision"] != expected_revision or row["fence"] != expected_fence:
            return OperationResult("FenceLost")
        if row["state"] != "DecisionPending":
            return OperationResult("IdentityConflict")
        await self._session.execute(
            text("""UPDATE employee_manager_receipts
            SET state=:s,decision_evidence=:d,observation_progress='Pending',revision=revision+1
            WHERE tenant_id=:t AND workspace_id=:w AND manager_target=:m AND manager_command_id=:c"""),
            {
                **params,
                "s": "AcceptedCommandPending" if accepted else "ManagerRejected",
                "d": decision_evidence,
            },
        )
        return OperationResult("Committed")
