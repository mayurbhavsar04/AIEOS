"""Static approved Skill catalog for the reference runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    skill_id: str
    skill_version_id: str
    capability_id: str
    capability_contract_version_id: str
    implementation_reference: str


class SkillRegistry:
    """Resolve immutable Skill metadata without executing Skill code."""

    def __init__(self, definitions: tuple[SkillDefinition, ...]) -> None:
        self._definitions = {item.skill_version_id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("duplicate SkillVersionId")

    def resolve(self, skill_version_id: str) -> SkillDefinition:
        try:
            return self._definitions[skill_version_id]
        except KeyError as error:
            raise LookupError(f"unknown SkillVersionId: {skill_version_id}") from error


__all__ = ("SkillDefinition", "SkillRegistry")
