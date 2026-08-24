# pyright: basic
"""Behavioral Draft 2020-12 and compatibility gate for Workflow AI budget governance."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "docs" / "architecture" / "schemas"


def load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


ENVELOPE_SCHEMA = load("workflow-ai-budget-envelope-v1.schema.json")
DEFINITION_SCHEMA = load("workflow-definition-v2.schema.json")
BINDING_SCHEMA = load("workflow-ai-budget-admission-binding-v1.schema.json")
DISPATCH_SCHEMA = load("dispatch-execution-attempt-v2.schema.json")
DEFINITION_ENVELOPE_URL = (
    "https://aieos.dev/contracts/workflow-definition/workflow-ai-budget-envelope-v1.schema.json"
)
DISPATCH_BINDING_URL = "https://aieos.dev/contracts/workflow-ai-budget-admission-binding/v1"
ENVELOPE = Draft202012Validator(ENVELOPE_SCHEMA)
BINDING = Draft202012Validator(BINDING_SCHEMA)
DEFINITION_REGISTRY = (
    Registry()
    .with_resource(ENVELOPE_SCHEMA["$id"], Resource.from_contents(ENVELOPE_SCHEMA))
    .with_resource(DEFINITION_ENVELOPE_URL, Resource.from_contents(ENVELOPE_SCHEMA))
)
DISPATCH_REGISTRY = Registry().with_resource(
    DISPATCH_BINDING_URL,
    Resource.from_contents(BINDING_SCHEMA),
)
DEFINITION = Draft202012Validator(
    DEFINITION_SCHEMA,
    registry=DEFINITION_REGISTRY,
)
DISPATCH = Draft202012Validator(
    DISPATCH_SCHEMA,
    registry=DISPATCH_REGISTRY,
)

NON_AI = "NON_AI"
AI_GATEWAY = "AI_GATEWAY"


class Rejected(ValueError):
    """A governed pre-dispatch rejection."""


@dataclass(frozen=True)
class CapabilityRoute:
    skill_version_id: str
    capability_id: str
    capability_contract_version_id: str
    route: str


CATALOG = {
    ("skill-ai-v1", "capability-classify", "capability-classify-v1"): CapabilityRoute(
        "skill-ai-v1",
        "capability-classify",
        "capability-classify-v1",
        AI_GATEWAY,
    ),
    ("skill-local-v1", "capability-validate", "capability-validate-v1"): CapabilityRoute(
        "skill-local-v1",
        "capability-validate",
        "capability-validate-v1",
        NON_AI,
    ),
}


def errors(validator: Draft202012Validator, value: dict) -> list[object]:
    if validator is DISPATCH:
        validator = Draft202012Validator(
            DISPATCH_SCHEMA,
            registry=DISPATCH_REGISTRY,
        )
    return list(validator.iter_errors(value))


def require_valid(validator: Draft202012Validator, value: dict) -> None:
    found = errors(validator, value)
    if found:
        raise AssertionError(found)


def require_invalid(validator: Draft202012Validator, value: dict) -> None:
    if not errors(validator, value):
        raise AssertionError(value)


def binding_for(route: str = AI_GATEWAY) -> dict:
    if route == AI_GATEWAY:
        skill_version_id = "skill-ai-v1"
        capability_id = "capability-classify"
        capability_contract_version_id = "capability-classify-v1"
    else:
        skill_version_id = "skill-local-v1"
        capability_id = "capability-validate"
        capability_contract_version_id = "capability-validate-v1"
    return {
        "BindingContractVersion": 1,
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "WorkflowId": "workflow-1",
        "WorkflowStepId": "step-1",
        "CommandId": "command-1",
        "ExecutionId": "execution-1",
        "WorkflowDefinitionVersionId": "wdv-1",
        "PolicyId": "policy-1",
        "PolicyVersionId": "policy-v1",
        "WorkflowAdmissionStateVersion": 7,
        "GatewayIdempotencyKey": "gateway-idempotency-1",
        "CommittedExposure": {"Amount": "1", "CurrencyOrReferenceUnit": "USD"},
        "CapabilityBinding": {
            "SkillVersionId": skill_version_id,
            "CapabilityId": capability_id,
            "CapabilityContractVersionId": capability_contract_version_id,
        },
    }


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


def definition(
    route: str = AI_GATEWAY,
    include_envelope: bool = True,
    contract_version: int = 2,
) -> dict:
    capability = binding_for(route)["CapabilityBinding"]
    value = {
        "DefinitionContractVersion": contract_version,
        "WorkflowDefinitionId": "wd-1",
        "WorkflowDefinitionVersionId": "wdv-1",
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "PolicyId": "policy-1",
        "PolicyVersionId": "policy-v1",
        "Steps": [
            {
                "WorkflowStepDefinitionId": "step-1",
                "CapabilityBinding": capability,
            }
        ],
    }
    if include_envelope:
        value["WorkflowAIBudgetEnvelope"] = envelope()
    return value


def legacy_definition(route: str) -> dict:
    return {
        "LegacyDefinition": True,
        "Steps": [
            {
                "WorkflowStepDefinitionId": "legacy-step-1",
                "CapabilityBinding": binding_for(route)["CapabilityBinding"],
            }
        ],
    }


def dispatch(binding: dict | None = None) -> dict:
    value = {
        "CommandId": "command-1",
        "CommandType": "DispatchExecutionAttempt",
        "CommandVersion": 2,
        "CorrelationId": "correlation-1",
        "CausationId": "cause-1",
        "WorkflowId": "workflow-1",
        "WorkflowStepId": "step-1",
        "ExecutionId": "execution-1",
        "TargetComponent": "Skill Runtime",
        "Initiator": "workflow-engine",
        "Timestamp": "2026-08-24T00:00:00Z",
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "Payload": {"statement": "classify this statement"},
        "Metadata": {
            "IdempotencyContext": {
                "GatewayIdempotencyKey": "gateway-idempotency-1",
            },
            "AuthorizationContext": {"DecisionId": "decision-1"},
            "AttemptContext": {"AttemptNumber": 1, "SkillVersionId": "skill-ai-v1"},
        },
    }
    if binding is not None:
        value["Metadata"]["WorkflowAIBudgetAdmissionBinding"] = binding
    return value


def resolve_route(capability_binding: dict) -> CapabilityRoute:
    skill_version_id = capability_binding.get("SkillVersionId")
    capability_id = capability_binding.get("CapabilityId")
    capability_contract_version_id = capability_binding.get("CapabilityContractVersionId")
    if not (
        isinstance(skill_version_id, str)
        and isinstance(capability_id, str)
        and isinstance(capability_contract_version_id, str)
    ):
        raise Rejected("immutable Skill/Capability route is malformed")
    key = (skill_version_id, capability_id, capability_contract_version_id)
    route = CATALOG.get(key)
    if route is None:
        raise Rejected("immutable Skill/Capability route is unknown")
    return route


def require_definition_acceptance(value: dict, *, legacy: bool = False) -> None:
    if not legacy and errors(DEFINITION, value):
        raise Rejected("Workflow Definition schema validation failed")
    routes = [resolve_route(step["CapabilityBinding"]).route for step in value["Steps"]]
    if legacy:
        if any(route != NON_AI for route in routes):
            raise Rejected("legacy AI-capable or unknown route has no governed envelope")
        return
    if any(route == AI_GATEWAY for route in routes) and "WorkflowAIBudgetEnvelope" not in value:
        raise Rejected("AI-capable v2 definition has no envelope")
    budget = value.get("WorkflowAIBudgetEnvelope")
    if budget is not None:
        if errors(ENVELOPE, budget):
            raise Rejected("Workflow AI Budget Envelope schema validation failed")
        for field in (
            "WorkflowDefinitionVersionId",
            "PolicyId",
            "PolicyVersionId",
            "TenantId",
            "WorkspaceId",
        ):
            if value[field] != budget[field]:
                raise Rejected(f"definition/envelope {field} mismatch")


def require_ai_gateway_handoff(
    command: dict,
    committed_admission: dict,
    *,
    expected_route: str,
) -> dict:
    if errors(DISPATCH, command):
        raise Rejected("DispatchExecutionAttempt schema validation failed")
    if expected_route != AI_GATEWAY:
        raise Rejected("this handoff validator is only for an AI Gateway route")
    binding = command["Metadata"].get("WorkflowAIBudgetAdmissionBinding")
    if binding is None:
        raise Rejected("AI Gateway dispatch has no Workflow admission binding")
    if errors(BINDING, binding):
        raise Rejected("Workflow admission binding schema validation failed")
    if committed_admission.get("State") != "Committed":
        raise Rejected("Workflow admission is not durably Committed")
    if binding != committed_admission.get("Binding"):
        raise Rejected("binding does not match durable committed Workflow admission")
    for field in (
        "TenantId",
        "WorkspaceId",
        "WorkflowId",
        "WorkflowStepId",
        "CommandId",
        "ExecutionId",
    ):
        if command[field] != binding[field]:
            raise Rejected(f"DispatchExecutionAttempt {field} mismatch")
    idempotency_context = command["Metadata"]["IdempotencyContext"]
    if idempotency_context.get("GatewayIdempotencyKey") != binding["GatewayIdempotencyKey"]:
        raise Rejected("Gateway idempotency context mismatch")
    if (
        command["Metadata"]["AttemptContext"]["SkillVersionId"]
        != binding["CapabilityBinding"]["SkillVersionId"]
    ):
        raise Rejected("Skill Version does not match admitted capability route")
    route = resolve_route(binding["CapabilityBinding"]).route
    if route != AI_GATEWAY:
        raise Rejected("admission binding does not resolve to AI Gateway")
    return binding


@dataclass
class GatewayAcceptanceFixture:
    """A durable acceptance/replay fixture, not a Gateway implementation."""

    accepted: dict[str, dict] = dataclass_field(default_factory=dict)
    acceptance_writes: int = 0

    def accept_or_replay(self, command: dict, committed_admission: dict) -> dict:
        binding = require_ai_gateway_handoff(
            command,
            committed_admission,
            expected_route=AI_GATEWAY,
        )
        key = binding["GatewayIdempotencyKey"]
        existing = self.accepted.get(key)
        if existing is not None:
            if existing["Binding"] != binding:
                raise Rejected("Gateway replay binding mismatch")
            return existing
        evidence = {
            "AIInvocationId": "ai-invocation-1",
            "Binding": deepcopy(binding),
            "ReservationOrEffectEvidence": None,
        }
        self.accepted[key] = evidence
        self.acceptance_writes += 1
        return evidence


def workflow_exposure(binding: dict, evidence: dict | None) -> str:
    """Return the exact scale-6 contribution required before another admission."""

    committed = binding["CommittedExposure"]
    if evidence is None:
        return committed["Amount"]
    if evidence.get("Binding") != binding:
        raise Rejected("Gateway evidence binding mismatch")
    gateway = evidence.get("ReservationOrEffectEvidence")
    if gateway is None:
        return committed["Amount"]
    if (
        gateway["TenantId"] != binding["TenantId"]
        or gateway["WorkspaceId"] != binding["WorkspaceId"]
    ):
        raise Rejected("Gateway evidence scope mismatch")
    if gateway["CurrencyOrReferenceUnit"] != committed["CurrencyOrReferenceUnit"]:
        raise Rejected("Gateway evidence unit mismatch")
    if gateway["State"] == "TERMINAL_RECONCILED":
        return gateway["Amount"]
    return max(
        committed["Amount"],
        gateway["Amount"],
        key=lambda amount: int(amount.replace(".", "").ljust(7, "0")),
    )


def assert_rejected(function: object, *args: object, **kwargs: object) -> None:
    try:
        function(*args, **kwargs)  # type: ignore[operator]
    except Rejected:
        return
    raise AssertionError("expected governed rejection")


def test_schema_semantics() -> None:
    for schema in (ENVELOPE_SCHEMA, DEFINITION_SCHEMA, BINDING_SCHEMA, DISPATCH_SCHEMA):
        Draft202012Validator.check_schema(schema)

    for amount in ("0.000001", "1", "999999999999999999", "999999999999.999999"):
        require_valid(ENVELOPE, envelope(amount))
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
        "1\n",
        "1\r",
        "1\r\n",
    ):
        require_invalid(ENVELOPE, envelope(amount))
    for unit in ("usd", " USD", "USD ", "EUR", "TOKEN", ""):
        require_invalid(ENVELOPE, envelope(unit=unit))

    for field in (
        "WorkflowDefinitionVersionId",
        "PolicyId",
        "PolicyVersionId",
        "TenantId",
        "WorkspaceId",
    ):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = envelope()
            malformed[field] = f"{malformed[field]}{terminal}"
            require_invalid(ENVELOPE, malformed)

    for field in (
        "WorkflowDefinitionId",
        "WorkflowDefinitionVersionId",
        "TenantId",
        "WorkspaceId",
        "PolicyId",
        "PolicyVersionId",
    ):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = definition()
            malformed[field] = f"{malformed[field]}{terminal}"
            require_invalid(DEFINITION, malformed)
    for field in (
        "WorkflowStepDefinitionId",
        "SkillVersionId",
        "CapabilityId",
        "CapabilityContractVersionId",
    ):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = definition()
            target = malformed["Steps"][0]
            if field == "WorkflowStepDefinitionId":
                target[field] = f"{target[field]}{terminal}"
            else:
                target["CapabilityBinding"][field] = (
                    f"{target['CapabilityBinding'][field]}{terminal}"
                )
            require_invalid(DEFINITION, malformed)

    for field in (
        "TenantId",
        "WorkspaceId",
        "WorkflowId",
        "WorkflowStepId",
        "CommandId",
        "ExecutionId",
        "WorkflowDefinitionVersionId",
        "PolicyId",
        "PolicyVersionId",
        "GatewayIdempotencyKey",
    ):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = binding_for()
            malformed[field] = f"{malformed[field]}{terminal}"
            require_invalid(BINDING, malformed)
    for field in ("SkillVersionId", "CapabilityId", "CapabilityContractVersionId"):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = binding_for()
            malformed["CapabilityBinding"][field] = (
                f"{malformed['CapabilityBinding'][field]}{terminal}"
            )
            require_invalid(BINDING, malformed)
    for terminal in ("\n", "\r", "\r\n"):
        malformed = binding_for()
        malformed["CommittedExposure"]["Amount"] = (
            f"{malformed['CommittedExposure']['Amount']}{terminal}"
        )
        require_invalid(BINDING, malformed)

    for field in (
        "CommandId",
        "CorrelationId",
        "CausationId",
        "WorkflowId",
        "WorkflowStepId",
        "ExecutionId",
        "Initiator",
        "TenantId",
        "WorkspaceId",
    ):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = dispatch(binding_for())
            malformed[field] = f"{malformed[field]}{terminal}"
            require_invalid(DISPATCH, malformed)
    positive_result_id = dispatch(binding_for())
    positive_result_id["Metadata"]["AuthoritativeResultId"] = "result-1"
    require_valid(DISPATCH, positive_result_id)
    for field in ("SkillVersionId",):
        for terminal in ("\n", "\r", "\r\n"):
            malformed = dispatch(binding_for())
            malformed["Metadata"]["AttemptContext"][field] = (
                f"{malformed['Metadata']['AttemptContext'][field]}{terminal}"
            )
            require_invalid(DISPATCH, malformed)
    for terminal in ("\n", "\r", "\r\n"):
        malformed = dispatch(binding_for())
        malformed["Metadata"]["AuthoritativeResultId"] = f"result-1{terminal}"
        require_invalid(DISPATCH, malformed)


def test_definition_compatibility() -> None:
    require_definition_acceptance(definition(AI_GATEWAY, include_envelope=True))
    assert_rejected(require_definition_acceptance, definition(AI_GATEWAY, include_envelope=False))

    false_non_ai = definition(AI_GATEWAY, include_envelope=False)
    false_non_ai["Steps"][0]["ExecutionBoundary"] = NON_AI
    require_valid(DEFINITION, false_non_ai)
    assert_rejected(require_definition_acceptance, false_non_ai)

    mismatch = definition(AI_GATEWAY)
    mismatch["WorkflowAIBudgetEnvelope"]["WorkspaceId"] = "other-workspace"
    assert_rejected(require_definition_acceptance, mismatch)

    unknown_envelope = definition(AI_GATEWAY)
    unknown_envelope["WorkflowAIBudgetEnvelope"]["ContractVersion"] = 2
    require_invalid(DEFINITION, unknown_envelope)
    assert_rejected(require_definition_acceptance, unknown_envelope)

    future = definition(AI_GATEWAY, contract_version=3)
    require_invalid(DEFINITION, future)
    assert_rejected(require_definition_acceptance, future)

    require_definition_acceptance(legacy_definition(NON_AI), legacy=True)
    assert_rejected(
        require_definition_acceptance,
        legacy_definition(AI_GATEWAY),
        legacy=True,
    )
    unknown_legacy = legacy_definition(NON_AI)
    unknown_legacy["Steps"][0]["CapabilityBinding"]["CapabilityId"] = "unknown-capability"
    assert_rejected(require_definition_acceptance, unknown_legacy, legacy=True)


def test_admission_binding_behavior() -> None:
    committed_binding = binding_for()
    committed = {"State": "Committed", "Binding": deepcopy(committed_binding)}
    accepted = GatewayAcceptanceFixture()

    first = accepted.accept_or_replay(dispatch(deepcopy(committed_binding)), committed)
    replay = accepted.accept_or_replay(dispatch(deepcopy(committed_binding)), committed)
    assert first["AIInvocationId"] == replay["AIInvocationId"]
    assert accepted.acceptance_writes == 1

    takeover = accepted.accept_or_replay(dispatch(deepcopy(committed_binding)), committed)
    assert takeover["AIInvocationId"] == first["AIInvocationId"]
    assert accepted.acceptance_writes == 1

    assert_rejected(accepted.accept_or_replay, dispatch(), committed)
    mismatched_command = dispatch(deepcopy(committed_binding))
    mismatched_command["Metadata"]["WorkflowAIBudgetAdmissionBinding"]["ExecutionId"] = (
        "other-execution"
    )
    assert_rejected(accepted.accept_or_replay, mismatched_command, committed)
    mismatched_context = dispatch(deepcopy(committed_binding))
    mismatched_context["Metadata"]["IdempotencyContext"]["GatewayIdempotencyKey"] = "other-key"
    assert_rejected(accepted.accept_or_replay, mismatched_context, committed)
    assert accepted.acceptance_writes == 1

    assert workflow_exposure(committed_binding, first) == "1"
    reserved = deepcopy(first)
    reserved["ReservationOrEffectEvidence"] = {
        "State": "RESERVED",
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "Amount": "0.5",
        "CurrencyOrReferenceUnit": "USD",
    }
    assert workflow_exposure(committed_binding, reserved) == "1"
    reserved["ReservationOrEffectEvidence"]["Amount"] = "2"
    assert workflow_exposure(committed_binding, reserved) == "2"
    terminal = deepcopy(reserved)
    terminal["ReservationOrEffectEvidence"] = {
        "State": "TERMINAL_RECONCILED",
        "TenantId": "tenant-1",
        "WorkspaceId": "workspace-1",
        "Amount": "0.4",
        "CurrencyOrReferenceUnit": "USD",
    }
    assert workflow_exposure(committed_binding, terminal) == "0.4"


def main() -> int:
    test_schema_semantics()
    test_definition_compatibility()
    test_admission_binding_behavior()
    print("workflow AI budget governance behavioral validator: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
