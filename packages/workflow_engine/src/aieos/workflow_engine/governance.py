"""Exact, Workflow-owned admission evidence for governed AI steps.

This module deliberately contains no provider pricing or reservation logic.  It
validates the approved v1 serialized shapes and records the conservative
Workflow commitment; Gateway remains the authority for provider accounting.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

_AMOUNT = re.compile(r"^(?:0\.[0-9]{0,5}[1-9]|[1-9][0-9]*(?:\.[0-9]{0,5}[1-9])?)$")
_OPAQUE_ID = re.compile(r"^[!-~]{1,256}$")


def scale6(value: str) -> int:
    """Parse the contract's canonical positive decimal without binary floats."""
    if not _AMOUNT.fullmatch(value):
        raise ValueError("Amount must be a canonical positive scale-6 decimal")
    whole, dot, fraction = value.partition(".")
    if len(whole) + len(fraction) > 18:
        raise ValueError("Amount must contain at most 18 total digits")
    return (
        int(whole) * 1_000_000 + int((fraction + "000000")[:6]) if dot else int(whole) * 1_000_000
    )


def _require_opaque_id(value: object, field: str) -> str:
    if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} must be a printable opaque identifier")
    return value


@dataclass(frozen=True, slots=True)
class WorkflowAIBudgetEnvelope:
    definition_version_id: str
    policy_id: str
    policy_version_id: str
    tenant_id: str
    workspace_id: str
    ceiling_microusd: int

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> WorkflowAIBudgetEnvelope:
        required = {
            "ContractVersion",
            "GatewayNormalizedCostUnitRegistryVersion",
            "WorkflowDefinitionVersionId",
            "PolicyId",
            "PolicyVersionId",
            "TenantId",
            "WorkspaceId",
            "BudgetCeiling",
        }
        if (
            set(value) != required
            or value.get("ContractVersion") != 1
            or value.get("GatewayNormalizedCostUnitRegistryVersion") != 1
        ):
            raise ValueError("unsupported WorkflowAIBudgetEnvelope")
        budget = value.get("BudgetCeiling")
        if (
            not isinstance(budget, Mapping)
            or set(budget) != {"Amount", "CurrencyOrReferenceUnit"}
            or budget.get("CurrencyOrReferenceUnit") != "USD"
        ):
            raise ValueError("unknown normalized cost unit")
        fields = (
            "WorkflowDefinitionVersionId",
            "PolicyId",
            "PolicyVersionId",
            "TenantId",
            "WorkspaceId",
        )
        identities = tuple(_require_opaque_id(value.get(field), field) for field in fields)
        amount = budget.get("Amount")
        if not isinstance(amount, str):
            raise ValueError("BudgetCeiling.Amount must be a string")
        return cls(*identities, scale6(amount))


@dataclass(frozen=True, slots=True)
class WorkflowAIAdmission:
    binding: Mapping[str, object]
    committed_microusd: int


def admission_binding(
    *,
    envelope: WorkflowAIBudgetEnvelope,
    workflow_id: str,
    workflow_step_id: str,
    command_id: str,
    execution_id: str,
    skill_version_id: str,
    capability_id: str,
    capability_contract_version_id: str,
    state_version: int,
    committed_microusd: int,
) -> dict[str, object]:
    """Produce the approved v1 fenced handoff shape, unchanged by Skill Runtime."""
    if committed_microusd <= 0:
        raise ValueError("committed exposure must be positive")
    amount = f"{committed_microusd // 1_000_000}.{committed_microusd % 1_000_000:06d}".rstrip(
        "0"
    ).rstrip(".")
    return {
        "BindingContractVersion": 1,
        "TenantId": envelope.tenant_id,
        "WorkspaceId": envelope.workspace_id,
        "WorkflowId": workflow_id,
        "WorkflowStepId": workflow_step_id,
        "CommandId": command_id,
        "ExecutionId": execution_id,
        "WorkflowDefinitionVersionId": envelope.definition_version_id,
        "PolicyId": envelope.policy_id,
        "PolicyVersionId": envelope.policy_version_id,
        "WorkflowAdmissionStateVersion": state_version,
        "GatewayIdempotencyKey": f"{workflow_id}:{workflow_step_id}:{command_id}:{execution_id}",
        "CommittedExposure": {"Amount": amount, "CurrencyOrReferenceUnit": "USD"},
        "CapabilityBinding": {
            "SkillVersionId": skill_version_id,
            "CapabilityId": capability_id,
            "CapabilityContractVersionId": capability_contract_version_id,
        },
    }


def validate_binding(
    value: Mapping[str, object],
    *,
    workflow_id: str,
    workflow_step_id: str,
    command_id: str,
    execution_id: str,
    tenant_id: str,
    workspace_id: str,
    skill_version_id: str,
    capability_id: str,
    capability_contract_version_id: str,
) -> None:
    if (
        value.get("BindingContractVersion") != 1
        or value.get("WorkflowId") != workflow_id
        or value.get("WorkflowStepId") != workflow_step_id
        or value.get("CommandId") != command_id
        or value.get("ExecutionId") != execution_id
        or value.get("TenantId") != tenant_id
        or value.get("WorkspaceId") != workspace_id
    ):
        raise ValueError("workflow AI admission binding lineage mismatch")
    capability = value.get("CapabilityBinding")
    committed = value.get("CommittedExposure")
    if (
        not isinstance(capability, Mapping)
        or not isinstance(committed, Mapping)
        or capability
        != {
            "SkillVersionId": skill_version_id,
            "CapabilityId": capability_id,
            "CapabilityContractVersionId": capability_contract_version_id,
        }
        or committed.get("CurrencyOrReferenceUnit") != "USD"
        or not isinstance(committed.get("Amount"), str)
    ):
        raise ValueError("workflow AI admission binding capability mismatch")
    scale6(committed["Amount"])
