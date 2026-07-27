"""Shared deterministic test configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """A mandatory PostgreSQL check may never silently skip in CI."""
    if (
        report.when == "call"
        and report.skipped
        and "postgres_required" in report.keywords
        and __import__("os").environ.get("CI")
    ):
        pytest.exit(f"critical PostgreSQL test skipped in CI: {report.nodeid}", returncode=1)
