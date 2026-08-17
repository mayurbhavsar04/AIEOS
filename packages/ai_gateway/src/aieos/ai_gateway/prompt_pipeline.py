"""Static, immutable Stage 1 prompt-package catalog owned by Prompt Pipeline."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import cast

# The single provider-neutral semantic definition for the governed Stage 1
# package.  Capability, Gateway, and provider adapters consume this only via
# the immutable catalog member and never maintain parallel task-kind fields.
STRUCTURED_TASK_KIND_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "task_kind": {"type": "string", "enum": ["Question", "Instruction", "Statement"]}
    },
    "required": ["task_kind"],
    "additionalProperties": False,
}


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in mapping.items()})
    if isinstance(value, list | tuple):
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(_deep_freeze(item) for item in sequence)
    return value


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): _json_value(item) for key, item in mapping.items()}
    if isinstance(value, tuple):
        sequence = cast(tuple[object, ...], value)
        return [_json_value(item) for item in sequence]
    return value


class PackageState(StrEnum):
    APPROVED = "Approved"
    CANDIDATE = "Candidate"
    DISABLED = "Disabled"


@dataclass(frozen=True, slots=True)
class PromptPackage:
    reference: str
    version_reference: str
    owner: str
    capability_id: str
    capability_contract_version_id: str
    typed_variables: tuple[tuple[str, str], ...]
    system_instruction_reference: str
    system_instruction: str
    output_schema_reference: str
    output_schema: Mapping[str, object]
    task_class: str
    evaluation_set_reference: str
    rollback_version_reference: str | None
    quality_threshold: Decimal
    per_class_recall_threshold: Decimal
    max_regression: Decimal
    max_input_tokens: int
    max_output_tokens: int
    max_cost: Decimal
    state: PackageState
    change_history: tuple[str, ...]

    def __post_init__(self) -> None:
        required = (
            self.reference,
            self.version_reference,
            self.owner,
            self.capability_id,
            self.capability_contract_version_id,
            self.system_instruction_reference,
            self.system_instruction,
            self.output_schema_reference,
            self.task_class,
            self.evaluation_set_reference,
        )
        if any(not value for value in required):
            raise ValueError("prompt-package references and content must be non-empty")
        if self.typed_variables != (("statement", "string[1..512]"),):
            raise ValueError("prompt-package typed variables do not match the governed contract")
        if (
            self.capability_id != "StructuredTaskKindClassification"
            or self.capability_contract_version_id != "1"
            or self.task_class != "classification"
        ):
            raise ValueError("prompt-package contract association is incompatible")
        if not self.change_history:
            raise ValueError("prompt-package change history is required")
        if not Decimal("0") <= self.quality_threshold <= Decimal("1"):
            raise ValueError("quality threshold must be within 0..1")
        if not Decimal("0") <= self.per_class_recall_threshold <= Decimal("1"):
            raise ValueError("per-class recall threshold must be within 0..1")
        if not Decimal("0") <= self.max_regression <= Decimal("1"):
            raise ValueError("maximum regression must be within 0..1")
        if (
            self.max_input_tokens != 256
            or self.max_output_tokens != 16
            or self.max_cost != Decimal("0.01")
        ):
            raise ValueError("prompt-package bounds do not match the governed contract")
        object.__setattr__(self, "output_schema", _deep_freeze(self.output_schema))

    @property
    def identity(self) -> str:
        material = json.dumps(
            {
                "reference": self.reference,
                "version": self.version_reference,
                "instruction": self.system_instruction,
                "schema": _json_value(self.output_schema),
                "variables": self.typed_variables,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AssembledPrompt:
    content: str
    package_identity: str
    output_schema_reference: str
    output_schema: Mapping[str, object]


class PromptPackageCatalog:
    """Resolve, bind, assemble, and select rollback without model execution."""

    def __init__(self, packages: tuple[PromptPackage, ...]) -> None:
        self._packages = {(item.reference, item.version_reference): item for item in packages}
        if len(self._packages) != len(packages):
            raise ValueError("duplicate prompt-package version")

    def resolve(
        self, reference: str, version_reference: str, *, executable: bool = True
    ) -> PromptPackage:
        try:
            package = self._packages[(reference, version_reference)]
        except KeyError as error:
            raise LookupError("prompt package is unknown, disabled, or incompatible") from error
        if executable and package.state is PackageState.DISABLED:
            raise LookupError("prompt package is unknown, disabled, or incompatible")
        return package

    def assemble(
        self,
        reference: str,
        version_reference: str,
        variables: Mapping[str, object],
    ) -> AssembledPrompt:
        package = self.resolve(reference, version_reference)
        if set(variables) != {"statement"} or not isinstance(variables["statement"], str):
            raise ValueError("prompt variables do not match the governed declaration")
        statement = variables["statement"].strip()
        if not 1 <= len(statement) <= 512:
            raise ValueError("statement binding is outside the governed contract")
        content = "\n".join(
            (
                f"<system ref='{package.system_instruction_reference}'>",
                package.system_instruction,
                "</system>",
                f"<task class='{package.task_class}'>",
                statement,
                "</task>",
                f"<schema ref='{package.output_schema_reference}'>",
                json.dumps(
                    _json_value(package.output_schema), sort_keys=True, separators=(",", ":")
                ),
                "</schema>",
            )
        )
        return AssembledPrompt(
            content, package.identity, package.output_schema_reference, package.output_schema
        )

    def schema(self, reference: str, version_reference: str) -> Mapping[str, object]:
        for package in self._packages.values():
            if (
                package.output_schema_reference == reference
                and package.version_reference == version_reference
                and package.state is not PackageState.DISABLED
            ):
                return package.output_schema
        raise LookupError("governed output schema is unresolved")

    def rollback(self, package: PromptPackage) -> PromptPackage | None:
        if package.rollback_version_reference is None:
            if any(
                item.reference == package.reference and item.state is PackageState.APPROVED
                for item in self._packages.values()
            ):
                raise LookupError(
                    "first-release rollback is invalid after an approved version exists"
                )
            return None
        target = self.resolve(package.reference, package.rollback_version_reference)
        if (
            target.state is not PackageState.APPROVED
            or target.version_reference == package.version_reference
        ):
            raise LookupError("approved immutable rollback target is unavailable")
        return target

    def release_selection(
        self,
        package: PromptPackage,
        *,
        accuracy: Decimal,
        per_class_recall: Mapping[str, Decimal],
        rollback_accuracy: Decimal | None,
        safety_and_schema_passed: bool,
    ) -> PromptPackage | None:
        if self._packages.get((package.reference, package.version_reference)) is not package:
            raise LookupError("candidate package is not an immutable catalog member")
        expected_classes = {"Question", "Instruction", "Statement"}
        if set(per_class_recall) != expected_classes:
            raise ValueError("release evidence must contain the exact governed class set")
        rollback = self.rollback(package)
        passed = (
            safety_and_schema_passed
            and accuracy >= package.quality_threshold
            and all(
                value >= package.per_class_recall_threshold for value in per_class_recall.values()
            )
            and (
                rollback_accuracy is None or rollback_accuracy - accuracy <= package.max_regression
            )
        )
        return package if passed else rollback


__all__ = (
    "STRUCTURED_TASK_KIND_SCHEMA",
    "AssembledPrompt",
    "PackageState",
    "PromptPackage",
    "PromptPackageCatalog",
)
