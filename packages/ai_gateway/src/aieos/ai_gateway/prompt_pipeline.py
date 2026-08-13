"""Static, immutable Stage 1 prompt-package catalog owned by Prompt Pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PromptPackage:
    reference: str
    version_reference: str
    owner: str
    capability_id: str
    capability_contract_version_id: str
    system_instruction_reference: str
    output_schema_reference: str
    evaluation_set_reference: str
    rollback_version_reference: str
    quality_threshold: Decimal
    max_input_tokens: int
    max_output_tokens: int
    max_cost: Decimal

    def __post_init__(self) -> None:
        required = (
            self.reference,
            self.version_reference,
            self.owner,
            self.capability_id,
            self.capability_contract_version_id,
            self.system_instruction_reference,
            self.output_schema_reference,
            self.evaluation_set_reference,
            self.rollback_version_reference,
        )
        if any(not value for value in required):
            raise ValueError("prompt-package references must be immutable and non-empty")
        if self.max_input_tokens <= 0 or self.max_output_tokens <= 0 or self.max_cost <= 0:
            raise ValueError("prompt-package bounds must be positive")


class PromptPackageCatalog:
    """Resolve static packages and approved rollback targets without model execution."""

    def __init__(self, packages: tuple[PromptPackage, ...]) -> None:
        self._packages = {(item.reference, item.version_reference): item for item in packages}
        if len(self._packages) != len(packages):
            raise ValueError("duplicate prompt-package version")

    def resolve(self, reference: str, version_reference: str) -> PromptPackage:
        try:
            return self._packages[(reference, version_reference)]
        except KeyError as error:
            raise LookupError("prompt package is unknown, disabled, or incompatible") from error

    def rollback(self, package: PromptPackage) -> PromptPackage:
        return self.resolve(package.reference, package.rollback_version_reference)


__all__ = ("PromptPackage", "PromptPackageCatalog")
