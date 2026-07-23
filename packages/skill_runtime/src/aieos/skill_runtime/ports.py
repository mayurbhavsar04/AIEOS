"""Skill Runtime public execution ports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from aieos.ai_gateway import AIGateway
from aieos.contracts import AuthorizationContext
from aieos.memory_service import MemoryService


@dataclass(frozen=True, slots=True)
class SkillInput:
    execution_id: str
    tenant_id: str
    workspace_id: str
    correlation_id: str
    causation_id: str
    authorization: AuthorizationContext
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SkillOutput:
    value: str
    memory_id: str
    ai_invocation_id: str


@dataclass(frozen=True, slots=True)
class SkillServices:
    ai_gateway: AIGateway
    memory_service: MemoryService


class Skill(Protocol):
    """Approved local Skill implementation."""

    async def execute(self, skill_input: SkillInput, services: SkillServices) -> SkillOutput: ...


class ExecutionAttemptRunner(Protocol):
    """Execute exactly one requested attempt; no retry-decision operation exists."""

    async def execute_attempt(self, execution_id: str) -> None: ...


__all__ = (
    "ExecutionAttemptRunner",
    "Skill",
    "SkillInput",
    "SkillOutput",
    "SkillServices",
)
