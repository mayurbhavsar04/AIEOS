"""Static allowlisted Capability resolution without execution authority."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilityImplementation:
    capability_id: str
    capability_contract_version_id: str
    implementation_reference: str
    boundary: str


class CapabilityRegistry:
    """Resolve one eligible implementation for an exact contract version."""

    def __init__(self, implementations: tuple[CapabilityImplementation, ...]) -> None:
        self._implementations = {
            (item.capability_id, item.capability_contract_version_id): item
            for item in implementations
        }
        if len(self._implementations) != len(implementations):
            raise ValueError("duplicate Capability contract registration")

    def resolve(
        self, capability_id: str, capability_contract_version_id: str
    ) -> CapabilityImplementation:
        try:
            return self._implementations[(capability_id, capability_contract_version_id)]
        except KeyError as error:
            raise LookupError(
                f"unsupported Capability contract: {capability_id}/{capability_contract_version_id}"
            ) from error


__all__ = ("CapabilityImplementation", "CapabilityRegistry")
