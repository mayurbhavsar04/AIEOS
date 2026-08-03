"""FastAPI host for health and the executable reference workflow."""

from typing import cast

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from aieos.ai_gateway import AIInvocationRequest, ResponseMode
from aieos_api.composition import CompositionRoot
from aieos_api.lifecycle import lifespan

app = FastAPI(title="AIEOS Reference Host", lifespan=lifespan)


class HelloRequest(BaseModel):
    message: str = Field(min_length=1)
    max_attempts: int = Field(default=2, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    command_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)


class MockAIRequest(BaseModel):
    prompt: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    structured: bool = False
    stream: bool = False
    quality_tier: int = Field(default=1, ge=1, le=3)
    max_output_tokens: int = Field(default=128, ge=1, le=4096)


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Expose runtime readiness only."""
    composition = cast(CompositionRoot, request.app.state.composition)
    return composition.health()


@app.get("/ready")
async def readiness(request: Request) -> dict[str, object]:
    """Check dependencies without mutating runtime state."""
    composition = cast(CompositionRoot, request.app.state.composition)
    return await composition.readiness()


@app.post("/reference/hello")
async def reference_hello(body: HelloRequest, request: Request) -> dict[str, object]:
    """Run HelloAIEOSWorkflow through all frozen component boundaries."""
    composition = cast(CompositionRoot, request.app.state.composition)
    result = await composition.reference_runtime.run(
        body.message,
        command_id=body.command_id,
        idempotency_key=body.idempotency_key,
        max_attempts=body.max_attempts,
        timeout_seconds=body.timeout_seconds,
    )
    return {
        "result_id": result.result_id,
        "status": result.result_status.value,
        "outcome": result.outcome_category.value,
        "value": result.value_reference,
        "error_id": result.error_id,
        "metadata": dict(result.metadata),
    }


@app.post("/reference/ai")
async def reference_ai(body: MockAIRequest, request: Request) -> dict[str, object]:
    """Run one offline provider-neutral AI Gateway reference invocation."""
    runtime = cast(CompositionRoot, request.app.state.composition).reference_runtime
    invocation = AIInvocationRequest(
        execution_id=runtime.identifiers.new("execution"),
        capability_contract_version_id="text-generation-v1",
        prompt=body.prompt,
        tenant_id=runtime.settings.tenant_id,
        workspace_id=runtime.settings.workspace_id,
        correlation_id=runtime.identifiers.new("correlation"),
        causation_id=runtime.identifiers.new("decision"),
        authorization=runtime.authorization,
        command_id=runtime.identifiers.new("command"),
        idempotency_key=body.idempotency_key,
        response_mode=ResponseMode.STRUCTURED if body.structured else ResponseMode.TEXT,
        output_schema_ref="reference-answer-v1" if body.structured else None,
        quality_tier=body.quality_tier,
        max_output_tokens=body.max_output_tokens,
    )
    response = await runtime.reference_ai_gateway.invoke(invocation)
    return {
        "ai_invocation_id": response.ai_invocation_id,
        "result_id": response.result.result_id,
        "status": response.result.result_status.value,
        "content": response.content,
        "error_id": response.result.error_id,
        "cache_hit": response.cache_hit,
        "route": response.route.model_key if response.route else None,
        "usage": None
        if response.usage is None
        else {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "estimated": response.usage.estimated,
        },
    }


def run() -> None:
    """Run the local host through the approved ASGI boundary."""
    import uvicorn

    uvicorn.run("aieos_api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
