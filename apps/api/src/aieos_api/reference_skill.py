"""Non-business reference Skill used to prove the AIEOS runtime flow."""

from __future__ import annotations

from aieos.ai_gateway import AIInvocationRequest
from aieos.contracts import ErrorCategory, RetryClassification
from aieos.memory_service import MemoryWrite
from aieos.skill_runtime import (
    SkillDependencyFailure,
    SkillInput,
    SkillOutput,
    SkillServices,
)


class HelloAIEOSSkill:
    """Exercise AI Gateway and Memory Service without product behavior."""

    async def execute(self, skill_input: SkillInput, services: SkillServices) -> SkillOutput:
        raw_message = skill_input.payload.get("message")
        if not isinstance(raw_message, str) or not raw_message.strip():
            raise ValueError("reference message must be a non-empty string")
        ai_response = await services.ai_gateway.invoke(
            AIInvocationRequest(
                execution_id=skill_input.execution_id,
                capability_contract_version_id="text-generation-v1",
                prompt=raw_message,
                tenant_id=skill_input.tenant_id,
                workspace_id=skill_input.workspace_id,
                correlation_id=skill_input.correlation_id,
                causation_id=skill_input.causation_id,
                authorization=skill_input.authorization,
            )
        )
        if ai_response.error is not None or ai_response.content is None:
            error = ai_response.error
            raise SkillDependencyFailure(
                error.message if error else "AI invocation returned no content",
                category=(error.error_category if error else ErrorCategory.AI_INVALID_RESPONSE),
                retry=(
                    error.retry_classification
                    if error
                    else RetryClassification.REQUIRES_POLICY_EVALUATION
                ),
            )
        memory = services.memory_service.store(
            MemoryWrite(
                content=ai_response.content,
                tenant_id=skill_input.tenant_id,
                workspace_id=skill_input.workspace_id,
                correlation_id=skill_input.correlation_id,
                provenance=ai_response.ai_invocation_id,
                authorization=skill_input.authorization,
            )
        )
        return SkillOutput(
            value=ai_response.content,
            memory_id=memory.memory_id,
            ai_invocation_id=ai_response.ai_invocation_id,
        )


__all__ = ("HelloAIEOSSkill",)
