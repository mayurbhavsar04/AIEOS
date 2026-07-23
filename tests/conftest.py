"""Shared deterministic test configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
