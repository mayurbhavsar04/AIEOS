"""Source-owned response evidence and frozen StartWorkflow union validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from pydantic import TypeAdapter

from aieos.contracts.results import ResultEnvelope, ResultStatus

from .employee_codec import UnsafeM7CommandValue, decode_safe_value, encode_safe_value, identifier

RESULT = TypeAdapter(ResultEnvelope)
TERMINAL_TAGS = frozenset(
    {"RejectedNoWorkflow", "TerminalWithWorkflow", "TerminalCapturedIdentityUnresolved"}
)
RESPONSE_TAGS = TERMINAL_TAGS | {"Acknowledged"}


@dataclass(frozen=True)
class WorkflowResponse:
    tag: str
    source_result: bytes
    source_result_digest: str
    source_profile: str
    source_error_reference: bytes | None = None
    workflow_id: str | None = None
    workflow_identity_proof: bytes | None = None

    def validate(self, tenant_id: str, workspace_id: str, command_id: str) -> ResultEnvelope:
        if self.tag not in RESPONSE_TAGS:
            raise UnsafeM7CommandValue("not a source StartWorkflow response")
        source = RESULT.validate_json(self.source_result)
        if (
            source.tenant_id,
            source.workspace_id,
            source.command_id,
            source.producer_component,
        ) != (tenant_id, workspace_id, command_id, "Workflow Engine"):
            raise UnsafeM7CommandValue("source response association mismatch")
        identifier(source.result_id)
        if self.source_profile != "M6-StartWorkflow-52271c4-v1" or source.contract_version != "1.0":
            raise UnsafeM7CommandValue("unsupported source response profile/version")
        if source.result_status not in {ResultStatus.ACCEPTED, ResultStatus.REJECTED}:
            raise UnsafeM7CommandValue("status not returned by frozen StartWorkflow")
        if source.causation_id != command_id:
            raise UnsafeM7CommandValue("source causation mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_result_digest):
            raise UnsafeM7CommandValue("invalid source Result digest")
        if self.tag == "Acknowledged":
            if source.result_status != ResultStatus.ACCEPTED:
                raise UnsafeM7CommandValue("acknowledgement requires source Accepted Result")
        elif source.result_status in {ResultStatus.ACCEPTED, ResultStatus.IN_PROGRESS}:
            raise UnsafeM7CommandValue("terminal tag requires terminal source")
        if self.tag == "RejectedNoWorkflow" and source.result_status != ResultStatus.REJECTED:
            raise UnsafeM7CommandValue("RejectedNoWorkflow requires source Rejection")
        if self.tag in {"Acknowledged", "TerminalWithWorkflow"}:
            identifier(self.workflow_id)
            if self.workflow_identity_proof is None:
                raise UnsafeM7CommandValue("missing source workflow identity proof")
            proof = decode_safe_value(self.workflow_identity_proof)
            if type(proof) is not dict:
                raise UnsafeM7CommandValue("malformed source workflow identity proof")
            fields = cast(dict[str, object], proof)
            expected = {
                "tenantId": tenant_id,
                "workspaceId": workspace_id,
                "startWorkflowCommandId": command_id,
                "sourceResultId": source.result_id,
                "workflowId": self.workflow_id,
            }
            if any(fields.get(key) != value for key, value in expected.items()) or not fields.get(
                "sourceEvidence"
            ):
                raise UnsafeM7CommandValue("source workflow identity proof mismatch")
        elif self.workflow_id is not None or self.workflow_identity_proof is not None:
            raise UnsafeM7CommandValue("workflow identity must remain absent")
        if source.error_id is not None:
            if self.source_error_reference is None:
                raise UnsafeM7CommandValue("missing source Error reference")
            reference = decode_safe_value(self.source_error_reference)
            if (
                type(reference) is not dict
                or cast(dict[str, object], reference).get("errorId") != source.error_id
            ):
                raise UnsafeM7CommandValue("source Error identity mismatch")
        return source

    def encode(self) -> bytes:
        return encode_safe_value(
            {
                "responseTag": self.tag,
                "sourceResult": self.source_result.decode("utf-8"),
                "sourceResultDigest": self.source_result_digest,
                "sourceProfile": self.source_profile,
                "sourceErrorReference": None
                if self.source_error_reference is None
                else decode_safe_value(self.source_error_reference),
                "workflowId": self.workflow_id,
                "workflowIdentityProof": None
                if self.workflow_identity_proof is None
                else decode_safe_value(self.workflow_identity_proof),
            }
        )


def reconstruct_response(evidence: bytes) -> WorkflowResponse:
    value = decode_safe_value(evidence)
    if type(value) is not dict:
        raise UnsafeM7CommandValue("invalid response evidence")
    fields = cast(dict[str, object], value)
    if set(fields) != {
        "responseTag",
        "sourceResult",
        "sourceResultDigest",
        "sourceProfile",
        "sourceErrorReference",
        "workflowId",
        "workflowIdentityProof",
    }:
        raise UnsafeM7CommandValue("incomplete response evidence")
    raw = fields["sourceResult"]
    if type(raw) is not str:
        raise UnsafeM7CommandValue("missing source Result")
    return WorkflowResponse(
        identifier(fields["responseTag"]),
        raw.encode("utf-8"),
        identifier(fields["sourceResultDigest"]),
        identifier(fields["sourceProfile"]),
        None
        if fields["sourceErrorReference"] is None
        else encode_safe_value(fields["sourceErrorReference"]),
        None if fields["workflowId"] is None else identifier(fields["workflowId"]),
        None
        if fields["workflowIdentityProof"] is None
        else encode_safe_value(fields["workflowIdentityProof"]),
    )
