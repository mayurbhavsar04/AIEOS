"""Adversarial frozen M7 command evidence tests, independent of database availability."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from aieos.adapters.persistence_postgres.employee_codec import (
    MAX_BYTES,
    MAX_INTEGER,
    UnsafeM7CommandValue,
    decode_safe_value,
    encode_safe_value,
    encode_start_workflow_evidence,
    reconstruct_start_workflow,
)
from aieos.contracts.commands.models import CommandEnvelope, CommandMetadata
from aieos.contracts.common import AuthorizationContext


def command() -> CommandEnvelope:
    return CommandEnvelope(
        "start-1",
        "StartWorkflow",
        "1.0",
        "correlation",
        "decision-1",
        "Workflow Engine",
        "Manager",
        datetime(2026, 9, 9, tzinfo=UTC),
        "t",
        "w",
        {
            "workflow_definition_id": "definition",
            "workflow_definition_version_id": "version",
            "skill_version_id": "skill",
            "nested": [None, {"b": 2, "a": list[object]()}],
        },
        CommandMetadata(
            "request",
            "start-key",
            AuthorizationContext(
                "actor", frozenset({"workflow.start", "read"}), "t", "w", "policy", "v1"
            ),
        ),
    )


@pytest.mark.parametrize(
    "value", [None, 0, 1, -MAX_INTEGER, MAX_INTEGER, "", "é", [], {}, [None, {"a": 1}]]
)
def test_safe_roundtrip(value: object) -> None:
    assert decode_safe_value(encode_safe_value(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        MAX_INTEGER + 1,
        -MAX_INTEGER - 1,
        (),
        (1,),
        {1},
        frozenset[object](),
        b"x",
        bytearray(b"x"),
        1.0,
        -0.0,
        float("nan"),
        float("inf"),
        Decimal("1"),
        object(),
        {1: "x"},
        "\0",
        "\ud800",
    ],
)
def test_unsupported_values(value: object) -> None:
    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value(value)


def test_subclasses_cycles_and_shared_containers() -> None:
    class Integer(int):
        pass

    class String(str):
        pass

    class List(list[object]):
        pass

    class Map(dict[str, object]):
        pass

    cycle: list[object] = []
    cycle.append(cycle)
    shared: list[object] = []
    for value in (
        Integer(1),
        String("x"),
        List(),
        Map(),
        {String("a"): 1},
        cycle,
        [shared, shared],
    ):
        with pytest.raises(UnsafeM7CommandValue):
            encode_safe_value(value)


def test_exact_limits() -> None:
    value: object = None
    for _ in range(32):
        value = [value]
    assert decode_safe_value(encode_safe_value(value)) == value
    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value([value])
    assert len(cast(list[object], decode_safe_value(encode_safe_value([None] * 9999)))) == 9999
    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value([None] * 10000)
    # Seven decimal length digits plus 's' and ':' occupy nine bytes.
    text = "x" * (MAX_BYTES - 9)
    assert len(encode_safe_value(text)) == MAX_BYTES
    assert decode_safe_value(encode_safe_value(text)) == text
    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value(text + "x")
    with pytest.raises(UnsafeM7CommandValue):
        decode_safe_value(b"s1048568:" + b"x" * 1048568)
    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value({str(i): None for i in range(5000)})


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"n",
        b"n;x",
        b"i-0;",
        b"i+1;",
        b"i01;",
        b"i1.0;",
        b"i1e0;",
        b"s01:x",
        b"s2:x",
        b"s1:\xff",
        b"t;",
        b"a10000:",
        b"o2:s1:bi1;s1:an;",
        b"o2:s1:an;s1:an;",
        b"o1:i1;n;",
    ],
)
def test_noncanonical_decoder_rejection(raw: bytes) -> None:
    with pytest.raises(UnsafeM7CommandValue):
        decode_safe_value(raw)


def test_governance_vector_and_map_order() -> None:
    assert encode_safe_value({"b": 1, "a": None}) == b"o2:s1:an;s1:bi1;"
    assert encode_safe_value({"x": [1, "x"], "a": {"é": 1, "z": 2}}) == encode_safe_value(
        {"a": {"z": 2, "é": 1}, "x": [1, "x"]}
    )


@pytest.mark.parametrize("attempt", [None, 1, 2147483647])
def test_complete_typed_roundtrip(attempt: int | None) -> None:
    original = command()
    original = replace(
        original,
        metadata=replace(
            original.metadata,
            attempt_number=attempt,
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            trace_id="trace",
            span_id="span",
        ),
    )
    evidence = encode_start_workflow_evidence(original)
    restored = reconstruct_start_workflow(evidence)
    assert restored == original
    assert (
        restored.metadata.attempt_number is attempt or restored.metadata.attempt_number == attempt
    )
    assert type(restored.timestamp) is datetime
    assert type(restored.metadata.authorization.permissions) is frozenset
    assert restored.payload is not original.payload
    assert encode_start_workflow_evidence(restored) == evidence


@pytest.mark.parametrize("attempt", [True, False, 0, -1, 2147483648, 1.0, "1"])
def test_invalid_attempt_evidence(attempt: object) -> None:
    basis = cast(dict[str, object], decode_safe_value(encode_start_workflow_evidence(command())))
    metadata = cast(dict[str, object], basis["metadata"])
    metadata["attempt_number"] = attempt
    with pytest.raises(UnsafeM7CommandValue):
        reconstruct_start_workflow(encode_safe_value(basis))


@pytest.mark.parametrize("section", ["envelope", "metadata", "authorization"])
def test_every_declared_field_is_required(section: str) -> None:
    basis = cast(dict[str, object], decode_safe_value(encode_start_workflow_evidence(command())))
    mapping = basis if section == "envelope" else cast(dict[str, object], basis["metadata"])
    if section == "authorization":
        mapping = cast(dict[str, object], mapping["authorization"])
    for field in list(mapping):
        value = mapping.pop(field)
        with pytest.raises(UnsafeM7CommandValue):
            reconstruct_start_workflow(encode_safe_value(basis))
        mapping[field] = value
    mapping["unknown"] = None
    with pytest.raises(UnsafeM7CommandValue):
        reconstruct_start_workflow(encode_safe_value(basis))


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_attempts", None),
        ("max_attempts", 0),
        ("max_attempts", 2147483648),
        ("timeout_seconds", 0),
        ("timeout_seconds", "1"),
        ("timeout_seconds", 1.0),
    ],
)
def test_payload_narrower_rules(key: str, value: object) -> None:
    original = command()
    with pytest.raises(UnsafeM7CommandValue):
        encode_start_workflow_evidence(replace(original, payload={**original.payload, key: value}))


def test_full_basis_preserves_null_absence_and_host_omitted_fields() -> None:
    original = command()
    first = encode_start_workflow_evidence(original)
    assert first != encode_start_workflow_evidence(replace(original, correlation_id="different"))
    assert first != encode_start_workflow_evidence(
        replace(original, metadata=replace(original.metadata, attempt_number=1))
    )
    assert first != encode_start_workflow_evidence(
        replace(original, payload={**original.payload, "extra": None})
    )
    assert "max_attempts" not in reconstruct_start_workflow(first).payload
