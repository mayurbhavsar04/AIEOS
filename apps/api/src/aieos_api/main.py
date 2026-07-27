"""FastAPI host for health and the executable reference workflow."""

from typing import cast

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from aieos_api.composition import CompositionRoot
from aieos_api.lifecycle import lifespan

app = FastAPI(title="AIEOS Reference Host", lifespan=lifespan)


class HelloRequest(BaseModel):
    message: str = Field(min_length=1)
    max_attempts: int = Field(default=2, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    command_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Expose runtime readiness only."""
    composition = cast(CompositionRoot, request.app.state.composition)
    return composition.health()


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


def run() -> None:
    """Run the local host through the approved ASGI boundary."""
    import uvicorn

    uvicorn.run("aieos_api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
