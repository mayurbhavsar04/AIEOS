"""Focused Draft 2020-12 and compatibility assertions for Workflow AI budget governance."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "docs" / "architecture" / "schemas"


def load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


ENVELOPE_SCHEMA = load("workflow-ai-budget-envelope-v1.schema.json")
DEFINITION_SCHEMA = load("workflow-definition-v2.schema.json")
RELATIVE_ENVELOPE_ID = (
    "https://aieos.dev/contracts/workflow-definition/workflow-ai-budget-envelope-v1.schema.json"
)
ENVELOPE = Draft202012Validator(ENVELOPE_SCHEMA)
DEFINITION = Draft202012Validator(
    DEFINITION_SCHEMA,
    resolver=RefResolver.from_schema(
        DEFINITION_SCHEMA,
        store={RELATIVE_ENVELOPE_ID: ENVELOPE_SCHEMA},
    ),
)


def envelope(amount: str = "1", unit: str = "USD") -> dict:
    return {
        "ContractVersion": 1,
        "GatewayNormalizedCostUnitRegistryVersion": 1,
        "WorkflowDefinitionVersionId": "wdv-1",
        "PolicyId": "policy-1",
        "PolicyVersionId": "policy-v1",
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "BudgetCeiling": {"Amount": amount, "CurrencyOrReferenceUnit": unit},
    }


def definition(ai: bool, include_envelope: bool = True) -> dict:
    value = {
        "DefinitionContractVersion": 2,
        "WorkflowDefinitionId": "wd-1",
        "WorkflowDefinitionVersionId": "wdv-1",
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "PolicyId": "policy-1",
        "PolicyVersionId": "policy-v1",
        "Steps": [
            {
                "WorkflowStepDefinitionId": "step-1",
                "ExecutionBoundary": "AI_GATEWAY" if ai else "NON_AI",
            }
        ],
    }
    if include_envelope:
        value["WorkflowAIBudgetEnvelope"] = envelope()
    return value


def valid(validator: Draft202012Validator, value: dict) -> None:
    errors = list(validator.iter_errors(value))
    assert not errors, errors


def invalid(validator: Draft202012Validator, value: dict) -> None:
    assert list(validator.iter_errors(value)), value


def source_scope_match(value: dict) -> bool:
    budget = value.get("WorkflowAIBudgetEnvelope")
    if not budget:
        return True
    return all(
        value[field] == budget[field]
        for field in (
            "WorkflowDefinitionVersionId",
            "PolicyId",
            "PolicyVersionId",
            "TenantId",
            "WorkspaceId",
        )
    )


def main() -> int:
    Draft202012Validator.check_schema(ENVELOPE_SCHEMA)
    Draft202012Validator.check_schema(DEFINITION_SCHEMA)

    for amount in ("0.000001", "1", "999999999999999999", "999999999999.999999"):
        valid(ENVELOPE, envelope(amount))
    for amount in (
        "0",
        "00",
        "0.0",
        "0.00",
        "-0",
        "-1",
        "+1",
        "01",
        "1.",
        ".1",
        "1.0",
        "1.00",
        "1e3",
        "1E3",
        "NaN",
        "Infinity",
        " 1",
        "1 ",
        "0.0000001",
        "9999999999999999999",
        "9999999999999.999999",
    ):
        invalid(ENVELOPE, envelope(amount))
    malformed = envelope()
    malformed["BudgetCeiling"]["Amount"] = 1
    invalid(ENVELOPE, malformed)
    for unit in ("usd", " USD", "USD ", "EUR", "TOKEN", ""):
        invalid(ENVELOPE, envelope(unit=unit))

    valid(DEFINITION, definition(ai=False, include_envelope=False))
    valid(DEFINITION, definition(ai=True, include_envelope=True))
    assert source_scope_match(definition(ai=True, include_envelope=True))
    invalid(DEFINITION, definition(ai=True, include_envelope=False))
    future = definition(ai=True)
    future["DefinitionContractVersion"] = 3
    invalid(DEFINITION, future)
    unknown_envelope = definition(ai=True)
    unknown_envelope["WorkflowAIBudgetEnvelope"]["ContractVersion"] = 2
    invalid(DEFINITION, unknown_envelope)
    mismatch = definition(ai=True)
    mismatch["WorkflowAIBudgetEnvelope"]["WorkspaceId"] = "other-workspace"
    assert not source_scope_match(mismatch)
    for bad_id in (
        " leading",
        "trailing ",
        "contains space",
        "line\nbreak",
        "tab\tvalue",
        "\x00control",
    ):
        bad = envelope()
        bad["PolicyId"] = bad_id
        invalid(ENVELOPE, bad)

    legacy_outcomes = {"PROVEN_NON_AI": True, "AI_GATEWAY": False, "UNKNOWN": False}
    assert legacy_outcomes == {"PROVEN_NON_AI": True, "AI_GATEWAY": False, "UNKNOWN": False}

    print("workflow AI budget schema and compatibility assertions: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
