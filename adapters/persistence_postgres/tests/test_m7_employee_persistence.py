import pytest

from aieos.adapters.persistence_postgres.employee import (
    DurableReference,
    Scope,
    UnsafeM7CommandValue,
    digest,
    encode_safe_value,
    encode_start_workflow_evidence,
    reconstruct_attempt_number,
    reconstruct_durable_reference,
)


def test_cd_digest_is_lowercase_and_domain_bound() -> None:
    first = digest("EmployeeAdmissionCallerInput", "v1", b"typed-value")
    second = digest("StartWorkflowCommand", "v1", b"typed-value")
    assert len(first.value) == 64
    assert first.value == first.value.lower()
    assert first.value != second.value
    assert first.domain != second.domain


def test_safe_values_preserve_bool_int_distinction_and_reject_subclasses() -> None:
    assert encode_safe_value(True) != encode_safe_value(1)

    class Integer(int):
        pass

    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value(Integer(1))


@pytest.mark.parametrize("value", [0, 2_147_483_648, True, 1.0])
def test_attempt_number_rejects_outside_corrected_domain(value: object) -> None:
    with pytest.raises(UnsafeM7CommandValue):
        encode_start_workflow_evidence({"attempt_number": value})


def test_attempt_none_is_present_null_and_distinct_from_one() -> None:
    none = encode_start_workflow_evidence({"attempt_number": None})
    one = encode_start_workflow_evidence({"attempt_number": 1})
    assert b"n;" in none and none != one
    with pytest.raises(UnsafeM7CommandValue):
        encode_start_workflow_evidence({})


def test_present_null_reconstructs_as_none() -> None:
    assert (
        reconstruct_attempt_number(encode_start_workflow_evidence({"attempt_number": None})) is None
    )
    assert reconstruct_attempt_number(encode_start_workflow_evidence({"attempt_number": 1})) == 1


def test_durable_reference_reconstruction_is_closed_and_scope_bound() -> None:
    scope = Scope("t", "w")
    reference = reconstruct_durable_reference(
        {"tenantId": "t", "workspaceId": "w", "kind": "Memory", "identity": "m"}, scope
    )
    assert reference == DurableReference("t", "w", "Memory", "m")
    with pytest.raises(UnsafeM7CommandValue):
        reconstruct_durable_reference(
            {"tenantId": "t", "workspaceId": "other", "kind": "Memory", "identity": "m"}, scope
        )


def test_safe_container_encoding_is_exact_and_key_order_independent() -> None:
    expected = b"o2:s1:aa2:t;i1;s1:bs1:x"
    assert encode_safe_value({"b": "x", "a": [True, 1]}) == expected
    assert encode_safe_value({"a": (True, 1), "b": "x"}) == expected


@pytest.mark.parametrize("value", [None, 1.0, {1: "x"}, {"x": None}, [object()], {1}, b"x"])
def test_safe_containers_reject_unsupported_keys_and_values(value: object) -> None:
    with pytest.raises(UnsafeM7CommandValue):
        encode_safe_value(value)


def test_safe_containers_reject_container_and_key_subclasses() -> None:
    class List(list[object]):
        pass

    class Map(dict[str, object]):
        pass

    class String(str):
        pass

    for value in (List(), Map(), {String("key"): 1}):
        with pytest.raises(UnsafeM7CommandValue):
            encode_safe_value(value)


@pytest.mark.parametrize("field", ["tenantId", "workspaceId", "kind", "identity"])
@pytest.mark.parametrize("value", [None, True, 1, [], {}, ""])
def test_durable_reference_validates_each_field_before_construction(
    field: str, value: object
) -> None:
    envelope: dict[str, object] = {
        "tenantId": "t",
        "workspaceId": "w",
        "kind": "Memory",
        "identity": "m",
    }
    envelope[field] = value
    with pytest.raises(UnsafeM7CommandValue, match="non-empty exact strings"):
        reconstruct_durable_reference(envelope, Scope("t", "w"))
