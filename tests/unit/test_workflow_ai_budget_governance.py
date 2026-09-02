"""Focused contract tests for Workflow AI budget admission values."""

import pytest

from aieos.workflow_engine.governance import WorkflowAIBudgetEnvelope, scale6


@pytest.mark.parametrize(
    ("serialized", "microusd"),
    [
        ("0.000001", 1),
        ("0.01", 10_000),
        ("1", 1_000_000),
        ("999999999999999999", 999_999_999_999_999_999_000_000),
        ("999999999999.999999", 999_999_999_999_999_999),
    ],
)
def test_scale6_is_exact(serialized: str, microusd: int) -> None:
    assert scale6(serialized) == microusd


@pytest.mark.parametrize(
    "serialized",
    [
        "0",
        "0.000000",
        "0.010",
        "01",
        "+1",
        "1.",
        "1e-6",
        " 1",
        "9999999999999.999999",
        "1000000000000000000",
    ],
)
def test_scale6_rejects_noncanonical_or_out_of_range_values(serialized: str) -> None:
    with pytest.raises(ValueError):
        scale6(serialized)


def test_envelope_snapshot_is_frozen_and_exactly_bound() -> None:
    envelope = WorkflowAIBudgetEnvelope.parse(
        {
            "ContractVersion": 1,
            "GatewayNormalizedCostUnitRegistryVersion": 1,
            "WorkflowDefinitionVersionId": "definition-v1",
            "PolicyId": "policy",
            "PolicyVersionId": "policy-v1",
            "TenantId": "tenant",
            "WorkspaceId": "workspace",
            "BudgetCeiling": {
                "Amount": "0.01",
                "CurrencyOrReferenceUnit": "USD",
            },
        }
    )

    assert envelope.ceiling_microusd == 10_000
    with pytest.raises(AttributeError):
        envelope.tenant_id = "other"  # type: ignore[misc]


def test_envelope_rejects_whitespace_identity() -> None:
    with pytest.raises(ValueError):
        WorkflowAIBudgetEnvelope.parse(
            {
                "ContractVersion": 1,
                "GatewayNormalizedCostUnitRegistryVersion": 1,
                "WorkflowDefinitionVersionId": "definition-v1",
                "PolicyId": "policy with spaces",
                "PolicyVersionId": "policy-v1",
                "TenantId": "tenant",
                "WorkspaceId": "workspace",
                "BudgetCeiling": {
                    "Amount": "0.01",
                    "CurrencyOrReferenceUnit": "USD",
                },
            }
        )
