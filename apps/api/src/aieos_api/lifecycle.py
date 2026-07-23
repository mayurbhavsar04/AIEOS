"""Host startup and shutdown lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aieos_api.composition import compose


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build and validate composition before accepting work."""
    app.state.composition = compose()
    yield
