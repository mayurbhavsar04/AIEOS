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
    STRUCTURED_TASK_KIND_PACKAGE,
    STRUCTURED_TASK_KIND_ROLLBACK_PACKAGE,
    CapabilityExecutionEvidence,
    CapabilityPolicyContext,
    EvaluationResult,
    StructuredTaskKindClassification,
    StructuredTaskKindInput,
    StructuredTaskKindResult,
    TaskKind,
    evaluate_predictions,
    exact_accuracy,
)

__all__ = (
    "STRUCTURED_TASK_KIND_PACKAGE",
    "STRUCTURED_TASK_KIND_ROLLBACK_PACKAGE",
    "CapabilityExecutionEvidence",
    "CapabilityPolicyContext",
    "EvaluationResult",
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
    "evaluate_predictions",
    "exact_accuracy",
)
