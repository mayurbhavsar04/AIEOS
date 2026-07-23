"""Minimal FastAPI host with health only; no business ingress is implemented."""

from typing import cast

from fastapi import FastAPI, Request

from aieos_api.composition import CompositionRoot
from aieos_api.lifecycle import lifespan

app = FastAPI(title="AIEOS Bootstrap Host", lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Expose bootstrap readiness only."""
    composition = cast(CompositionRoot, request.app.state.composition)
    return composition.health()


def run() -> None:
    """Run the local host through the approved ASGI boundary."""
    import uvicorn

    uvicorn.run("aieos_api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
