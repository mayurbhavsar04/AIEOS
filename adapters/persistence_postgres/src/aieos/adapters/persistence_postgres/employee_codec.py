"""Frozen M7 safe evidence codec; no business-object or reference resolver.

Authority: Safe Command Value Domain CTO Decision sections E-J, Correction 1.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import cast

from aieos.contracts.commands.models import CommandEnvelope, CommandMetadata
from aieos.contracts.common import AuthorizationContext
from aieos.workflow_engine.governance import WorkflowAIBudgetEnvelope

SAFE_PROFILE = "M7SafeCommandValueV1"
EVIDENCE_PROFILE = "M7SafeCommandEvidenceV1"
REPLAY_PATH = "FrozenM6PostgresWorkflowHost52271c4"
MAX_INTEGER = 9_007_199_254_740_991
MAX_NODES = 10_000
MAX_BYTES = 1_048_576
MAX_DEPTH = 32


class UnsafeM7CommandValue(ValueError):
    """InvalidInput: reject before equality, durable intent, or dispatch."""


def encode_safe_value(value: object) -> bytes:
    """Encode the exact finite tree, counting keys and rejecting container aliases."""
    seen: set[int] = set()
    output = bytearray()
    nodes = 0

    def emit(raw: bytes) -> None:
        if len(output) + len(raw) > MAX_BYTES:
            raise UnsafeM7CommandValue("encoded byte limit exceeded")
        output.extend(raw)

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES:
            raise UnsafeM7CommandValue("node limit exceeded")
        if item is None:
            emit(b"n;")
        elif type(item) is int:
            if not -MAX_INTEGER <= item <= MAX_INTEGER:
                raise UnsafeM7CommandValue("integer outside safe bounds")
            emit(b"i" + str(item).encode("ascii") + b";")
        elif type(item) is str:
            if len(item) > MAX_BYTES:
                raise UnsafeM7CommandValue("encoded byte limit exceeded")
            if "\0" in item:
                raise UnsafeM7CommandValue("NUL is forbidden")
            try:
                raw = item.encode("utf-8", "strict")
            except UnicodeError as error:
                raise UnsafeM7CommandValue("invalid Unicode scalar string") from error
            emit(b"s" + str(len(raw)).encode("ascii") + b":" + raw)
        elif type(item) in (list, dict):
            if depth >= MAX_DEPTH or id(item) in seen:
                raise UnsafeM7CommandValue("container depth, cycle, or shared container")
            seen.add(id(item))
            if type(item) is list:
                items = cast(list[object], item)
                emit(b"a" + str(len(items)).encode("ascii") + b":")
                for child in items:
                    visit(child, depth + 1)
            else:
                mapping = cast(dict[object, object], item)
                if 2 * len(mapping) > MAX_NODES - nodes:
                    raise UnsafeM7CommandValue("node limit exceeded")
                keys: list[str] = []
                for key in mapping:
                    if type(key) is not str:
                        raise UnsafeM7CommandValue("keys must be exact strings")
                    try:
                        key.encode("utf-8", "strict")
                    except UnicodeError as error:
                        raise UnsafeM7CommandValue("invalid key Unicode") from error
                    keys.append(key)
                emit(b"o" + str(len(keys)).encode("ascii") + b":")
                for key in sorted(keys, key=lambda key: key.encode("utf-8")):
                    visit(key, depth + 1)
                    visit(mapping[key], depth + 1)
        else:
            raise UnsafeM7CommandValue("unsupported M7 safe value")

    visit(value, 0)
    return bytes(output)


def decode_safe_value(evidence: bytes) -> object:
    """Strict canonical decoder: bounds apply before allocation or recursion."""
    if type(evidence) is not bytes or len(evidence) > MAX_BYTES:
        raise UnsafeM7CommandValue("invalid evidence bytes")
    offset = 0
    nodes = 0

    def number(delimiter: bytes, signed: bool = False) -> int:
        nonlocal offset
        end = evidence.find(delimiter, offset)
        if end < 0 or end - offset > 17:
            raise UnsafeM7CommandValue("invalid length/integer")
        raw = evidence[offset:end]
        pattern = rb"(?:0|-[1-9][0-9]*|[1-9][0-9]*)" if signed else rb"(?:0|[1-9][0-9]*)"
        if not re.fullmatch(pattern, raw):
            raise UnsafeM7CommandValue("noncanonical number")
        offset = end + 1
        return int(raw)

    def parse(depth: int) -> object:
        nonlocal offset, nodes
        nodes += 1
        if nodes > MAX_NODES or offset >= len(evidence):
            raise UnsafeM7CommandValue("truncated evidence or node limit")
        tag = evidence[offset : offset + 1]
        offset += 1
        if tag == b"n":
            if evidence[offset : offset + 1] != b";":
                raise UnsafeM7CommandValue("invalid null")
            offset += 1
            return None
        if tag == b"i":
            value = number(b";", True)
            if abs(value) > MAX_INTEGER:
                raise UnsafeM7CommandValue("integer outside safe bounds")
            return value
        if tag == b"s":
            length = number(b":")
            if length > len(evidence) - offset:
                raise UnsafeM7CommandValue("truncated string")
            try:
                value = evidence[offset : offset + length].decode("utf-8", "strict")
            except UnicodeError as error:
                raise UnsafeM7CommandValue("invalid UTF-8") from error
            offset += length
            if "\0" in value:
                raise UnsafeM7CommandValue("NUL is forbidden")
            return value
        if tag not in (b"a", b"o") or depth >= MAX_DEPTH:
            raise UnsafeM7CommandValue("unsupported tag or container depth")
        count = number(b":")
        if count * (2 if tag == b"o" else 1) > MAX_NODES - nodes:
            raise UnsafeM7CommandValue("node limit exceeded")
        if tag == b"a":
            return [parse(depth + 1) for _ in range(count)]
        result: dict[str, object] = {}
        previous: bytes | None = None
        for _ in range(count):
            key = parse(depth + 1)
            if type(key) is not str:
                raise UnsafeM7CommandValue("map key must be string")
            raw = key.encode("utf-8")
            if previous is not None and raw <= previous:
                raise UnsafeM7CommandValue("unsorted or duplicate map key")
            previous = raw
            result[key] = parse(depth + 1)
        return result

    value = parse(0)
    if offset != len(evidence):
        raise UnsafeM7CommandValue("trailing evidence")
    return value


def identifier(value: object, limit: int = 128) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise UnsafeM7CommandValue("identifier must be non-empty exact bounded string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise UnsafeM7CommandValue("identifier contains control character")
    encode_safe_value(value)
    return value


def _permission(value: object) -> str:
    if type(value) is not str:
        raise UnsafeM7CommandValue("permission must be exact string")
    encode_safe_value(value)
    return value


def _optional_id(value: object) -> str | None:
    return None if value is None else identifier(value)


def _instant(value: object) -> str:
    if type(value) is not datetime or value.tzinfo is not UTC or value.fold != 0:
        raise UnsafeM7CommandValue("typed timestamp must be exact UTC fold-0 datetime")
    return (
        f"{value.year:04}-{value.month:02}-{value.day:02}T"
        f"{value.hour:02}:{value.minute:02}:{value.second:02}.{value.microsecond:06}Z"
    )


def _restore_instant(value: object) -> datetime:
    if type(value) is not str or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value
    ):
        raise UnsafeM7CommandValue("invalid timestamp basis")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise UnsafeM7CommandValue("invalid timestamp") from error


def _map(value: object, fields: set[str] | None = None) -> dict[str, object]:
    if type(value) is not dict:
        raise UnsafeM7CommandValue("expected exact map")
    result = cast(dict[str, object], value)
    if fields is not None and result.keys() != fields:
        raise UnsafeM7CommandValue("incomplete or unknown closed fields")
    return result


ENVELOPE_FIELDS = set(
    [
        "command_id",
        "command_type",
        "command_version",
        "correlation_id",
        "causation_id",
        "target_component",
        "initiator",
        "timestamp",
        "tenant_id",
        "workspace_id",
        "payload",
        "metadata",
        "workflow_id",
        "workflow_step_id",
        "execution_id",
    ]
)
METADATA_FIELDS = set(
    [
        "request_id",
        "idempotency_key",
        "authorization",
        "attempt_number",
        "expires_at",
        "trace_id",
        "span_id",
        "skill_version_id",
        "authoritative_result_id",
        "workflow_ai_budget_admission",
    ]
)
AUTH_FIELDS = set(
    ["actor_id", "permissions", "tenant_id", "workspace_id", "policy_id", "policy_version_id"]
)


def _attempt(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise UnsafeM7CommandValue("attempt_number must be None or exact int 1..2147483647")
    return value


def encode_start_workflow_evidence(command: CommandEnvelope) -> bytes:
    """Explicit positional mapping of every frozen field, with no reflection."""
    if type(command) is not CommandEnvelope or type(command.metadata) is not CommandMetadata:
        raise UnsafeM7CommandValue("expected frozen typed command")
    metadata = command.metadata
    auth = metadata.authorization
    if type(auth) is not AuthorizationContext or type(auth.permissions) is not frozenset:
        raise UnsafeM7CommandValue("expected frozen typed authorization")
    permissions = [_permission(p) for p in auth.permissions]
    basis: dict[str, object] = {
        "command_id": command.command_id,
        "command_type": command.command_type,
        "command_version": command.command_version,
        "correlation_id": command.correlation_id,
        "causation_id": command.causation_id,
        "target_component": command.target_component,
        "initiator": command.initiator,
        "timestamp": _instant(command.timestamp),
        "tenant_id": command.tenant_id,
        "workspace_id": command.workspace_id,
        "payload": command.payload,
        "workflow_id": command.workflow_id,
        "workflow_step_id": command.workflow_step_id,
        "execution_id": command.execution_id,
        "metadata": {
            "request_id": metadata.request_id,
            "idempotency_key": metadata.idempotency_key,
            "authorization": {
                "actor_id": auth.actor_id,
                "permissions": sorted(permissions, key=lambda p: p.encode("utf-8")),
                "tenant_id": auth.tenant_id,
                "workspace_id": auth.workspace_id,
                "policy_id": auth.policy_id,
                "policy_version_id": auth.policy_version_id,
            },
            "attempt_number": _attempt(metadata.attempt_number),
            "expires_at": None if metadata.expires_at is None else _instant(metadata.expires_at),
            "trace_id": metadata.trace_id,
            "span_id": metadata.span_id,
            "skill_version_id": metadata.skill_version_id,
            "authoritative_result_id": metadata.authoritative_result_id,
            "workflow_ai_budget_admission": metadata.workflow_ai_budget_admission,
        },
    }
    evidence = encode_safe_value(basis)
    reconstruct_start_workflow(evidence)
    return evidence


def reconstruct_start_workflow(evidence: bytes) -> CommandEnvelope:
    """Reconstruct and validate the entire immutable command, never fill defaults."""
    basis = _map(decode_safe_value(evidence), ENVELOPE_FIELDS)
    metadata = _map(basis["metadata"], METADATA_FIELDS)
    auth = _map(metadata["authorization"], AUTH_FIELDS)
    raw_permissions = auth["permissions"]
    if type(raw_permissions) is not list:
        raise UnsafeM7CommandValue("permissions must be positional list")
    permissions = [_permission(p) for p in cast(list[object], raw_permissions)]
    if permissions != sorted(set(permissions), key=lambda p: p.encode("utf-8")):
        raise UnsafeM7CommandValue("duplicate or noncanonical permissions")
    payload = _map(basis["payload"])
    if "max_attempts" in payload and (_attempt(payload["max_attempts"]) is None):
        raise UnsafeM7CommandValue("max_attempts cannot be null")
    if "timeout_seconds" in payload:
        timeout = payload["timeout_seconds"]
        if type(timeout) is not int or timeout <= 0:
            raise UnsafeM7CommandValue("timeout must be positive exact safe integer")
    for key in ("workflow_definition_id", "workflow_definition_version_id", "skill_version_id"):
        identifier(payload.get(key))
    if "workflow_ai_budget_envelope" in payload:
        try:
            envelope = WorkflowAIBudgetEnvelope.parse(_map(payload["workflow_ai_budget_envelope"]))
            if (envelope.tenant_id, envelope.workspace_id, envelope.definition_version_id) != (
                basis["tenant_id"],
                basis["workspace_id"],
                payload["workflow_definition_version_id"],
            ):
                raise UnsafeM7CommandValue("budget envelope scope/definition mismatch")
        except ValueError as error:
            raise UnsafeM7CommandValue(str(error)) from error
    budget = metadata["workflow_ai_budget_admission"]
    if budget is not None:
        _map(budget)
    if (
        basis["command_type"] != "StartWorkflow"
        or basis["command_version"] not in {"1", "1.0", "2", "2.0"}
        or basis["target_component"] != "Workflow Engine"
    ):
        raise UnsafeM7CommandValue("unsupported frozen StartWorkflow contract")
    try:
        return CommandEnvelope(
            command_id=identifier(basis["command_id"]),
            command_type="StartWorkflow",
            command_version=identifier(basis["command_version"]),
            correlation_id=identifier(basis["correlation_id"]),
            causation_id=identifier(basis["causation_id"]),
            target_component="Workflow Engine",
            initiator=identifier(basis["initiator"]),
            timestamp=_restore_instant(basis["timestamp"]),
            tenant_id=identifier(basis["tenant_id"]),
            workspace_id=identifier(basis["workspace_id"]),
            payload=payload,
            workflow_id=_optional_id(basis["workflow_id"]),
            workflow_step_id=_optional_id(basis["workflow_step_id"]),
            execution_id=_optional_id(basis["execution_id"]),
            metadata=CommandMetadata(
                request_id=identifier(metadata["request_id"]),
                idempotency_key=identifier(metadata["idempotency_key"], 256),
                authorization=AuthorizationContext(
                    actor_id=identifier(auth["actor_id"]),
                    permissions=frozenset(permissions),
                    tenant_id=identifier(auth["tenant_id"]),
                    workspace_id=identifier(auth["workspace_id"]),
                    policy_id=identifier(auth["policy_id"]),
                    policy_version_id=identifier(auth["policy_version_id"]),
                ),
                attempt_number=_attempt(metadata["attempt_number"]),
                expires_at=None
                if metadata["expires_at"] is None
                else _restore_instant(metadata["expires_at"]),
                trace_id=_optional_id(metadata["trace_id"]),
                span_id=_optional_id(metadata["span_id"]),
                skill_version_id=_optional_id(metadata["skill_version_id"]),
                authoritative_result_id=_optional_id(metadata["authoritative_result_id"]),
                workflow_ai_budget_admission=None if budget is None else _map(budget),
            ),
        )
    except ValueError as error:
        raise UnsafeM7CommandValue(str(error)) from error


def reconstruct_attempt_number(evidence: bytes) -> int | None:
    return reconstruct_start_workflow(evidence).metadata.attempt_number
