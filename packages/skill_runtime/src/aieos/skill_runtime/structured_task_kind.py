"""Approved ES-016 governed structured capability."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import cast

from aieos.ai_gateway import (
    STRUCTURED_TASK_KIND_SCHEMA,
    AIInvocationRequest,
    AIInvocationResponse,
    PackageState,
    PromptPackage,
    PromptPackageCatalog,
    ResponseMode,
)
from aieos.capability_registry import CapabilityImplementation
from aieos.contracts import DataClassification, ResultStatus, RetryClassification
from aieos.skill_runtime.ports import SkillInput, SkillOutput, SkillServices
from aieos.skill_runtime.runtime import SkillDependencyFailure


class TaskKind(StrEnum):
    QUESTION = "Question"
    INSTRUCTION = "Instruction"
    STATEMENT = "Statement"


@dataclass(frozen=True, slots=True)
class StructuredTaskKindInput:
    statement: str

    @classmethod
    def parse(cls, payload: Mapping[str, object]) -> StructuredTaskKindInput:
        if set(payload) != {"statement"}:
            raise ValueError("structured task input must contain exactly statement")
        statement = payload.get("statement")
        if not isinstance(statement, str):
            raise ValueError("statement must be a string")
        normalized = statement.strip()
        if not 1 <= len(normalized) <= 512:
            raise ValueError("statement must contain 1..512 Unicode scalar values")
        return cls(normalized)


@dataclass(frozen=True, slots=True)
class StructuredTaskKindResult:
    task_kind: TaskKind

    @classmethod
    def accept(cls, content: str | None) -> StructuredTaskKindResult:
        if content is None:
            raise ValueError("Gateway returned no canonical structured content")
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError("Gateway canonical result is not structured JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Gateway canonical result has an incompatible field set")
        structured = cast(dict[str, object], value)
        if set(structured) != {"task_kind"}:
            raise ValueError("Gateway canonical result has an incompatible field set")
        try:
            task_kind = TaskKind(structured["task_kind"])
        except (TypeError, ValueError) as error:
            raise ValueError("Gateway canonical result has an invalid task kind") from error
        return cls(task_kind)

    def canonical_json(self) -> str:
        return json.dumps({"task_kind": self.task_kind.value}, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class CapabilityExecutionEvidence:
    execution_id: str
    capability_id: str
    contract_version: str
    prompt_package_ref: str
    prompt_package_version_ref: str
    disposition: str
    terminal_outcome: str
    tenant_id: str
    workspace_id: str
    data_classification: DataClassification
    redaction_applied: bool
    ai_invocation_id: str | None = None
    bypass_reason: str | None = None
    avoided_input_tokens: int = 0
    avoided_output_tokens: int = 0
    avoided_cost: Decimal = Decimal("0")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    governed_cost: Decimal | None = None
    route_reference: str | None = None
    primary_gateway_invocations: int = 0
    provider_attempts: int | None = None
    repair_attempts: int | None = None
    fallback_attempts: int | None = None
    total_model_calls: int | None = None


@dataclass(frozen=True, slots=True)
class CapabilityPolicyContext:
    data_classification: DataClassification
    safety_policy_ref: str
    cache_policy_ref: str
    budget_policy_ref: str
    residency: str
    required_data_handling: frozenset[str]
    minimum_security_tier: int

    def __post_init__(self) -> None:
        if (
            not all(
                (
                    self.safety_policy_ref,
                    self.cache_policy_ref,
                    self.budget_policy_ref,
                    self.residency,
                )
            )
            or self.minimum_security_tier <= 0
        ):
            raise ValueError("verified capability policy context is incomplete")


STRUCTURED_TASK_KIND_PACKAGE = PromptPackage(
    reference="structured-task-kind",
    version_reference="v1",
    owner="Prompt Pipeline",
    capability_id="StructuredTaskKindClassification",
    capability_contract_version_id="1",
    typed_variables=(("statement", "string[1..512]"),),
    system_instruction_reference="structured-task-kind-system-v1",
    system_instruction=(
        "Classify only the communicative form of the task. Return exactly one "
        "task_kind enum value; "
        "do not infer intent, priority, topic, sentiment, authority, or workflow routing."
    ),
    output_schema_reference="structured-task-kind-schema-v1",
    output_schema=STRUCTURED_TASK_KIND_SCHEMA,
    task_class="classification",
    evaluation_set_reference="structured-task-kind-protected-v1",
    rollback_version_reference=None,
    quality_threshold=Decimal("0.95"),
    per_class_recall_threshold=Decimal("0.90"),
    max_regression=Decimal("0.02"),
    max_input_tokens=256,
    max_output_tokens=16,
    max_cost=Decimal("0.01"),
    state=PackageState.CANDIDATE,
    change_history=("v1 first-release candidate: minimum-sufficient wording",),
)


class StructuredTaskKindClassification:
    """Execute one model-free bypass or exactly one provider-neutral Gateway invocation."""

    capability_id = "StructuredTaskKindClassification"
    contract_version = "1"

    def __init__(
        self,
        *,
        prompt_packages: PromptPackageCatalog | None = None,
        authoritative_results: Mapping[str, TaskKind] | None = None,
        policy_context: CapabilityPolicyContext | None = None,
    ) -> None:
        self._packages = prompt_packages or PromptPackageCatalog((STRUCTURED_TASK_KIND_PACKAGE,))
        self._authoritative_results = dict(authoritative_results or {})
        self._policy_context = policy_context

    def validate_registry_binding(self, capability: CapabilityImplementation) -> None:
        package = self._packages.resolve("structured-task-kind", "v1")
        if (
            capability.capability_id != self.capability_id
            or capability.capability_contract_version_id != self.contract_version
            or capability.prompt_package_ref != package.reference
            or capability.prompt_package_version_ref != package.version_reference
            or capability.output_schema_ref != package.output_schema_reference
            or package.identity != STRUCTURED_TASK_KIND_PACKAGE.identity
        ):
            raise ValueError("immutable capability package/schema binding mismatch")

    @staticmethod
    def validate_reused_output(value: str) -> str:
        return StructuredTaskKindResult.accept(value).canonical_json()

    async def execute(self, skill_input: SkillInput, services: SkillServices) -> SkillOutput:
        typed_input = StructuredTaskKindInput.parse(skill_input.payload)
        self._require_security_context(skill_input)
        package = self._packages.resolve("structured-task-kind", "v1")
        authoritative = self._authoritative_results.get(skill_input.execution_id)
        policy = self._require_policy_context()
        classification = policy.data_classification
        if authoritative is not None:
            result = StructuredTaskKindResult(authoritative)
            return SkillOutput(result.canonical_json(), "", "")

        admission = skill_input.workflow_ai_budget_admission
        gateway_idempotency_key = (
            admission.get("GatewayIdempotencyKey")
            if admission is not None
            else skill_input.execution_id
        )
        if not isinstance(gateway_idempotency_key, str):
            raise ValueError("governed AI execution requires a Gateway admission binding")

        response = await services.ai_gateway.invoke(
            AIInvocationRequest(
                execution_id=skill_input.execution_id,
                capability_id=self.capability_id,
                capability_contract_version_id=self.contract_version,
                prompt=typed_input.statement,
                tenant_id=skill_input.tenant_id,
                workspace_id=skill_input.workspace_id,
                correlation_id=skill_input.correlation_id,
                causation_id=skill_input.causation_id,
                authorization=skill_input.authorization,
                command_id=skill_input.causation_id,
                idempotency_key=gateway_idempotency_key,
                prompt_template_ref=package.reference,
                prompt_template_version_ref=package.version_reference,
                system_instruction_ref=package.system_instruction_reference,
                response_mode=ResponseMode.STRUCTURED,
                output_schema_ref=package.output_schema_reference,
                output_schema=package.output_schema,
                output_schema_identity=package.identity,
                required_capabilities=frozenset({"structured"}),
                max_input_tokens=package.max_input_tokens,
                max_output_tokens=package.max_output_tokens,
                max_total_cost=package.max_cost,
                context_items=(),
                data_classification=classification,
                safety_policy_ref=policy.safety_policy_ref,
                cache_policy_ref=policy.cache_policy_ref,
                budget_policy_ref=policy.budget_policy_ref,
                residency=policy.residency,
                required_data_handling=policy.required_data_handling,
                minimum_security_tier=policy.minimum_security_tier,
                allow_fallback=True,
                max_provider_attempts=2,
                repair_attempts=1,
                cache_allowed=False,
                workflow_ai_budget_admission=admission,
            )
        )
        return self._complete_ai_path(skill_input, package, response, classification)

    def _complete_ai_path(
        self,
        skill_input: SkillInput,
        package: PromptPackage,
        response: AIInvocationResponse,
        classification: DataClassification,
    ) -> SkillOutput:
        if response.result.result_status is not ResultStatus.SUCCEEDED:
            raise SkillDependencyFailure.from_gateway(response)
        try:
            result = StructuredTaskKindResult.accept(response.content)
        except ValueError as error:
            raise SkillDependencyFailure(
                "Canonical structured result failed deterministic capability acceptance",
                retry=RetryClassification.NEVER_RETRY,
            ) from error
        return SkillOutput(result.canonical_json(), "", response.ai_invocation_id)

    @staticmethod
    def _require_security_context(skill_input: SkillInput) -> None:
        authorization = skill_input.authorization
        if (
            authorization.tenant_id != skill_input.tenant_id
            or authorization.workspace_id != skill_input.workspace_id
            or "ai.invoke" not in authorization.permissions
        ):
            raise ValueError("missing or scope-mismatched verified security context")

    def _require_policy_context(self) -> CapabilityPolicyContext:
        if self._policy_context is None:
            raise ValueError("verified capability policy context is missing")
        return self._policy_context


def exact_accuracy(
    expected: tuple[TaskKind, ...], actual: tuple[TaskKind, ...], *, threshold: Decimal
) -> bool:
    """Objective, model-free release gate for protected fixtures."""
    if not expected or len(expected) != len(actual):
        return False
    correct = sum(left is right for left, right in zip(expected, actual, strict=True))
    return Decimal(correct) / Decimal(len(expected)) >= threshold


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    accuracy: Decimal
    per_class_recall: Mapping[TaskKind, Decimal]
    confusion: Mapping[TaskKind, Mapping[TaskKind, int]]
    passed: bool


def evaluate_predictions(
    expected: tuple[TaskKind, ...], actual: tuple[TaskKind, ...]
) -> EvaluationResult:
    if not expected or len(expected) != len(actual):
        return EvaluationResult(Decimal("0"), {}, {}, False)
    correct = sum(left is right for left, right in zip(expected, actual, strict=True))
    accuracy = Decimal(correct) / Decimal(len(expected))
    recall = {
        kind: Decimal(
            sum(
                left is kind and right is kind for left, right in zip(expected, actual, strict=True)
            )
        )
        / Decimal(sum(left is kind for left in expected))
        for kind in TaskKind
    }
    confusion = {
        expected_kind: {
            actual_kind: sum(
                left is expected_kind and right is actual_kind
                for left, right in zip(expected, actual, strict=True)
            )
            for actual_kind in TaskKind
        }
        for expected_kind in TaskKind
    }
    return EvaluationResult(
        accuracy,
        recall,
        confusion,
        accuracy >= Decimal("0.95") and all(value >= Decimal("0.90") for value in recall.values()),
    )


__all__ = (
    "STRUCTURED_TASK_KIND_PACKAGE",
    "CapabilityExecutionEvidence",
    "CapabilityPolicyContext",
    "EvaluationResult",
    "StructuredTaskKindClassification",
    "StructuredTaskKindInput",
    "StructuredTaskKindResult",
    "TaskKind",
    "evaluate_predictions",
    "exact_accuracy",
)
