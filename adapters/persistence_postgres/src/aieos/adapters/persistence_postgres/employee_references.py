"""C-owned retention of owner-governed references, without resolving business content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from .employee_codec import UnsafeM7CommandValue, decode_safe_value, encode_safe_value, identifier


@dataclass(frozen=True)
class DurableReference:
    tenant_id: str
    workspace_id: str
    kind: str
    identity: str
    owning_contract: str
    contract_version: str
    immutable_pin: str
    provenance: str
    classification: str
    allowed_purpose: str
    # Exact owner-defined scope, pin semantics and validation metadata, retained intact.
    owner_evidence: bytes


class ReferenceOwner(Protocol):
    async def validate_use(self, reference: DurableReference, purpose: str) -> bytes:
        """Recheck current access/retention/integrity; return exact authoritative evidence.

        The existing owning contract implements this port; failure must raise.
        This is not a generic loader and does not return business content.
        """
        ...


def encode_durable_reference(reference: DurableReference) -> bytes:
    if type(reference) is not DurableReference:
        raise UnsafeM7CommandValue("invalid reference type")
    return encode_safe_value(
        {
            "tenantId": identifier(reference.tenant_id),
            "workspaceId": identifier(reference.workspace_id),
            "kind": identifier(reference.kind),
            "identity": identifier(reference.identity),
            "owningContract": identifier(reference.owning_contract),
            "contractVersion": identifier(reference.contract_version),
            "immutablePin": identifier(reference.immutable_pin, 256),
            "provenance": identifier(reference.provenance, 256),
            "classification": identifier(reference.classification),
            "allowedPurpose": identifier(reference.allowed_purpose),
            "ownerEvidence": decode_safe_value(reference.owner_evidence),
        }
    )


def reconstruct_durable_reference(value: object, scope: object) -> DurableReference:
    # Scope is accepted structurally only at this persistence helper's public boundary.
    from .employee import Scope

    if type(scope) is not Scope or type(value) is not dict:
        raise UnsafeM7CommandValue("invalid durable reference envelope/scope")
    fields = cast(dict[str, object], value)
    if set(fields) != {
        "tenantId",
        "workspaceId",
        "kind",
        "identity",
        "owningContract",
        "contractVersion",
        "immutablePin",
        "provenance",
        "classification",
        "allowedPurpose",
        "ownerEvidence",
    }:
        raise UnsafeM7CommandValue("incomplete durable reference")
    reference = DurableReference(
        identifier(fields["tenantId"]),
        identifier(fields["workspaceId"]),
        identifier(fields["kind"]),
        identifier(fields["identity"]),
        identifier(fields["owningContract"]),
        identifier(fields["contractVersion"]),
        identifier(fields["immutablePin"], 256),
        identifier(fields["provenance"], 256),
        identifier(fields["classification"]),
        identifier(fields["allowedPurpose"]),
        encode_safe_value(fields["ownerEvidence"]),
    )
    if reference.tenant_id != scope.tenant_id or reference.workspace_id != scope.workspace_id:
        raise UnsafeM7CommandValue("durable reference scope mismatch")
    if type(fields["ownerEvidence"]) is not dict or not fields["ownerEvidence"]:
        raise UnsafeM7CommandValue("missing owner validation metadata")
    return reference
