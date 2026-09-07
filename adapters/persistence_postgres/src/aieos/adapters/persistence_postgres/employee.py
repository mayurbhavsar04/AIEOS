"""M7-C durable persistence ports and PostgreSQL implementation.

This module deliberately contains no catalog, admission, or Manager decisions.  It
only preserves the immutable facts and fenced transitions supplied by M7-D/E.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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


class UnsafeM7CommandValue(ValueError):
    """Raised before persistence for a value outside the closed M7 safe domain."""


@dataclass(frozen=True)
class DurableReference:
    tenant_id: str
    workspace_id: str
    kind: str
    identity: str


def encode_durable_reference(reference: DurableReference) -> bytes:
    if not all(
        type(value) is str and value
        for value in (
            reference.tenant_id,
            reference.workspace_id,
            reference.kind,
            reference.identity,
        )
    ):
        raise UnsafeM7CommandValue("durable reference fields must be non-empty exact strings")
    return encode_safe_value(
        {
            "tenantId": reference.tenant_id,
            "workspaceId": reference.workspace_id,
            "kind": reference.kind,
            "identity": reference.identity,
        }
    )


def reconstruct_durable_reference(value: object, scope: "Scope") -> DurableReference:
    if type(value) is not dict or set(value) != {"tenantId", "workspaceId", "kind", "identity"}:
        raise UnsafeM7CommandValue("invalid durable reference envelope")
    reference = DurableReference(
        value["tenantId"], value["workspaceId"], value["kind"], value["identity"]
    )
    encode_durable_reference(reference)
    if reference.tenant_id != scope.tenant_id or reference.workspace_id != scope.workspace_id:
        raise UnsafeM7CommandValue("durable reference scope mismatch")
    return reference


def encode_safe_value(value: object, *, depth: int = 0) -> bytes:
    """Encode only the closed CD-v1 safe-value algebra.

    This deliberately uses exact type checks: ``True`` is never the integer one,
    subclasses are never silently normalized, and a typed-envelope null is handled
    by ``encode_start_workflow_evidence`` rather than broadening this algebra.
    """
    if depth > 32:
        raise UnsafeM7CommandValue("M7 command nesting exceeds 32")
    if type(value) is bool:
        return b"t;" if value else b"f;"
    if type(value) is int:
        return b"i" + str(value).encode("ascii") + b";"
    if type(value) is str:
        raw = value.encode("utf-8")
        return b"s" + str(len(raw)).encode("ascii") + b":" + raw
    if type(value) is list or type(value) is tuple:
        return (
            b"a"
            + str(len(value)).encode("ascii")
            + b":"
            + b"".join(encode_safe_value(item, depth=depth + 1) for item in value)
        )
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise UnsafeM7CommandValue("M7 object keys must be exact strings")
        members = []
        for key in sorted(value):
            members.append(encode_safe_value(key, depth=depth + 1))
            members.append(encode_safe_value(value[key], depth=depth + 1))
        return b"o" + str(len(value)).encode("ascii") + b":" + b"".join(members)
    raise UnsafeM7CommandValue(f"unsupported M7 safe value: {type(value).__name__}")


def encode_start_workflow_evidence(metadata: dict[str, object]) -> bytes:
    """Encode the closed metadata map with its one approved present-null field."""
    if "attempt_number" not in metadata:
        raise UnsafeM7CommandValue("attempt_number must be present in complete evidence")
    attempt = metadata["attempt_number"]
    if attempt is None:
        encoded_attempt = b"n;"
    elif type(attempt) is int and 1 <= attempt <= 2_147_483_647:
        encoded_attempt = encode_safe_value(attempt)
    else:
        raise UnsafeM7CommandValue("attempt_number must be None or exact int 1..2147483647")
    remainder = {key: value for key, value in metadata.items() if key != "attempt_number"}
    return b"m" + encoded_attempt + encode_safe_value(remainder)


def reconstruct_attempt_number(evidence: bytes) -> int | None:
    """Recover only the frozen typed field; malformed evidence fails closed."""
    if not evidence.startswith(b"m"):
        raise UnsafeM7CommandValue("invalid StartWorkflow evidence")
    marker = evidence[1:]
    if marker.startswith(b"n;"):
        return None
    if not marker.startswith(b"i") or b";" not in marker:
        raise UnsafeM7CommandValue("invalid attempt_number evidence")
    raw = marker[1 : marker.index(b";")]
    try:
        value = int(raw.decode("ascii"))
    except ValueError as error:
        raise UnsafeM7CommandValue("invalid attempt_number evidence") from error
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise UnsafeM7CommandValue("invalid attempt_number evidence")
    return value


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
    value: dict[str, Any] | None = None


class PostgresEmployeePersistence:
    """C-owned SCB A-L storage operations.

    The generic immutable-fact primitive backs observations, source evidence,
    quarantine and administrative facts.  The database constraints remain the
    authoritative concurrency guard; callers must provide verified scope.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
                    "p": PROFILE,
                    "f": fingerprint,
                },
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Opened")
        old = (
            await self._session.execute(
                text(
                    """SELECT complete_command FROM employee_manager_receipts WHERE tenant_id=:t AND workspace_id=:w AND manager_target=:m AND manager_command_id=:c"""
                ),
                {"t": scope.tenant_id, "w": scope.workspace_id, "m": target, "c": command_id},
            )
        ).scalar_one()
        return OperationResult("Existing" if old == complete_command else "IdentityConflict")

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
            return OperationResult("Existing")
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
        """CAS/fence transition; callers supply only a contract-valid state bundle."""
        row = (
            (
                await self._session.execute(
                    text("""UPDATE employee_manager_receipts
          SET state=:s, revision=revision+1 WHERE tenant_id=:t AND workspace_id=:w
          AND manager_target=:m AND manager_command_id=:c AND revision=:r AND fence=:f
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
                    text("""SELECT r.complete_command,r.command_profile,r.state,
          c.complete_evidence,c.replay_path FROM employee_manager_receipts r
          LEFT JOIN employee_start_workflow_commands c ON c.tenant_id=r.tenant_id
          AND c.workspace_id=r.workspace_id AND c.idempotency_key=r.idempotency_key
          WHERE r.tenant_id=:t AND r.workspace_id=:w AND r.manager_target=:m AND r.manager_command_id=:c"""),
                    {"t": scope.tenant_id, "w": scope.workspace_id, "m": target, "c": command_id},
                )
            )
            .mappings()
            .first()
        )
        return OperationResult("Recovered" if row else "NotFound", dict(row) if row else None)

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
        return OperationResult("Resolved" if row is not None else "ExistingResolution")

    async def capture_response(
        self,
        scope: Scope,
        source_identity: str,
        response_tag: str,
        evidence: bytes,
        workflow_id: str | None = None,
    ) -> OperationResult:
        if response_tag not in RESPONSE_TAGS:
            raise ValueError("unknown frozen StartWorkflow response tag")
        requires_workflow = {"Acknowledged", "TerminalWithWorkflow"}
        forbids_workflow = {
            "ManagerRejected",
            "AcceptedCommandPending",
            "RejectedNoWorkflow",
            "TerminalCapturedIdentityUnresolved",
        }
        if (response_tag in requires_workflow and not workflow_id) or (
            response_tag in forbids_workflow and workflow_id is not None
        ):
            raise ValueError("invalid frozen WorkflowId condition")
        return await self.append_observation(
            scope, "WorkflowStartResponse", source_identity, evidence
        )

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
        """Create or load immutable command evidence; fingerprint is stored only as a hint."""
        row = (
            await self._session.execute(
                text("""
          INSERT INTO employee_start_workflow_commands
          (tenant_id,workspace_id,command_id,idempotency_key,complete_evidence,fingerprint,replay_path)
          VALUES (:t,:w,:c,:k,:e,:f,:p)
          ON CONFLICT (tenant_id,workspace_id,command_id) DO NOTHING
          RETURNING complete_evidence"""),
                {
                    "t": scope.tenant_id,
                    "w": scope.workspace_id,
                    "c": command_id,
                    "k": idempotency_key,
                    "e": complete_evidence,
                    "f": fingerprint,
                    "p": replay_path,
                },
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Created")
        existing = (
            await self._session.execute(
                text("""SELECT complete_evidence
          FROM employee_start_workflow_commands WHERE tenant_id=:t AND workspace_id=:w AND command_id=:c"""),
                {"t": scope.tenant_id, "w": scope.workspace_id, "c": command_id},
            )
        ).scalar_one()
        return OperationResult("Existing" if existing == complete_evidence else "CommandConflict")

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
          ON CONFLICT (tenant_id,workspace_id,principal_id,idempotency_key) DO NOTHING
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
            .one()
        )
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
                    p=PROFILE,
                    n=source.domain,
                    x=payload,
                ),
            )
        ).scalar_one_or_none()
        if row is not None:
            return OperationResult("Registered")
        old = (
            await self._session.execute(
                text(
                    "SELECT digest FROM employee_source_evidence WHERE tenant_id=:t AND workspace_id=:w AND source_component=:c AND source_contract_version=:v AND source_kind=:k AND source_id=:i"
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
        ).scalar_one()
        if old == source.value:
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
                e=old,
                n=source.value,
            ),
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
