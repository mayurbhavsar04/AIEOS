"""Approved ES-016 governed structured capability."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import cast

from aieos.ai_gateway import (
    AIInvocationRequest,
    AIInvocationResponse,
    PromptPackage,
    PromptPackageCatalog,
    ResponseMode,
)
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


STRUCTURED_TASK_KIND_PACKAGE = PromptPackage(
    reference="structured-task-kind",
    version_reference="v1",
    owner="Prompt Pipeline",
    capability_id="StructuredTaskKindClassification",
    capability_contract_version_id="1",
    system_instruction_reference="structured-task-kind-system-v1",
    output_schema_reference="structured-task-kind-schema-v1",
    evaluation_set_reference="structured-task-kind-protected-v1",
    rollback_version_reference="v1",
    quality_threshold=Decimal("0.95"),
    max_input_tokens=256,
    max_output_tokens=16,
    max_cost=Decimal("0.01"),
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
    ) -> None:
        self._packages = prompt_packages or PromptPackageCatalog((STRUCTURED_TASK_KIND_PACKAGE,))
        self._authoritative_results = dict(authoritative_results or {})
        self.evidence: dict[str, CapabilityExecutionEvidence] = {}

    async def execute(self, skill_input: SkillInput, services: SkillServices) -> SkillOutput:
        typed_input = StructuredTaskKindInput.parse(skill_input.payload)
        self._require_security_context(skill_input)
        package = self._packages.resolve("structured-task-kind", "v1")
        authoritative = self._authoritative_results.get(skill_input.execution_id)
        classification = self._classification(skill_input.payload)
        if authoritative is not None:
            result = StructuredTaskKindResult(authoritative)
            self.evidence[skill_input.execution_id] = CapabilityExecutionEvidence(
                execution_id=skill_input.execution_id,
                capability_id=self.capability_id,
                contract_version=self.contract_version,
                prompt_package_ref=package.reference,
                prompt_package_version_ref=package.version_reference,
                disposition="bypassed",
                terminal_outcome="Succeeded",
                tenant_id=skill_input.tenant_id,
                workspace_id=skill_input.workspace_id,
                data_classification=classification,
                redaction_applied=True,
                bypass_reason="authoritative_result_reuse",
                avoided_input_tokens=package.max_input_tokens,
                avoided_output_tokens=package.max_output_tokens,
                avoided_cost=package.max_cost,
            )
            return SkillOutput(result.canonical_json(), "", "")

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
                idempotency_key=skill_input.execution_id,
                prompt_template_ref=package.reference,
                prompt_template_version_ref=package.version_reference,
                system_instruction_ref=package.system_instruction_reference,
                response_mode=ResponseMode.STRUCTURED,
                output_schema_ref=package.output_schema_reference,
                required_capabilities=frozenset({"structured"}),
                max_input_tokens=package.max_input_tokens,
                max_output_tokens=package.max_output_tokens,
                max_total_cost=package.max_cost,
                context_items=(),
                data_classification=classification,
                allow_fallback=True,
                max_provider_attempts=2,
                repair_attempts=1,
                cache_allowed=False,
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
        usage = response.usage
        route = response.route
        self.evidence[skill_input.execution_id] = CapabilityExecutionEvidence(
            execution_id=skill_input.execution_id,
            capability_id=self.capability_id,
            contract_version=self.contract_version,
            prompt_package_ref=package.reference,
            prompt_package_version_ref=package.version_reference,
            disposition="invoked",
            terminal_outcome=response.result.result_status.value,
            tenant_id=skill_input.tenant_id,
            workspace_id=skill_input.workspace_id,
            data_classification=classification,
            redaction_applied=True,
            ai_invocation_id=response.ai_invocation_id,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_tokens=usage.cached_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            governed_cost=route.estimated_cost if route else None,
            route_reference=route.decision_reference if route else None,
            primary_gateway_invocations=1,
        )
        if response.result.result_status is not ResultStatus.SUCCEEDED:
            raise SkillDependencyFailure(
                "AI Gateway returned a normalized terminal failure",
                retry=RetryClassification.REQUIRES_POLICY_EVALUATION,
            )
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

    @staticmethod
    def _classification(payload: Mapping[str, object]) -> DataClassification:
        value = payload.get("data_classification")
        if not isinstance(value, str):
            raise ValueError("data_classification is required")
        try:
            return DataClassification(value)
        except ValueError as error:
            raise ValueError("unknown data_classification") from error


def exact_accuracy(
    expected: tuple[TaskKind, ...], actual: tuple[TaskKind, ...], *, threshold: Decimal
) -> bool:
    """Objective, model-free release gate for protected fixtures."""
    if not expected or len(expected) != len(actual):
        return False
    correct = sum(left is right for left, right in zip(expected, actual, strict=True))
    return Decimal(correct) / Decimal(len(expected)) >= threshold


__all__ = (
    "STRUCTURED_TASK_KIND_PACKAGE",
    "CapabilityExecutionEvidence",
    "StructuredTaskKindClassification",
    "StructuredTaskKindInput",
    "StructuredTaskKindResult",
    "TaskKind",
    "exact_accuracy",
)
