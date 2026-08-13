"""Skill Runtime that executes one instructed attempt at a time."""

from aieos.skill_runtime.ports import (
    ExecutionAttemptRunner,
    Skill,
    SkillInput,
    SkillOutput,
    SkillServices,
)
from aieos.skill_runtime.runtime import (
    ExecutionRecord,
    ExecutionState,
    InMemoryExecutionRepository,
    SkillDependencyFailure,
    SkillRuntime,
)
from aieos.skill_runtime.structured_task_kind import (
    CapabilityExecutionEvidence,
    StructuredTaskKindClassification,
    StructuredTaskKindInput,
    StructuredTaskKindResult,
    TaskKind,
    exact_accuracy,
)

__all__ = (
    "CapabilityExecutionEvidence",
    "ExecutionAttemptRunner",
    "ExecutionRecord",
    "ExecutionState",
    "InMemoryExecutionRepository",
    "Skill",
    "SkillDependencyFailure",
    "SkillInput",
    "SkillOutput",
    "SkillRuntime",
    "SkillServices",
    "StructuredTaskKindClassification",
    "StructuredTaskKindInput",
    "StructuredTaskKindResult",
    "TaskKind",
    "exact_accuracy",
)
