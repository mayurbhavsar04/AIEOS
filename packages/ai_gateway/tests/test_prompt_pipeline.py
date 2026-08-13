"""Prompt Pipeline static package governance tests."""

from decimal import Decimal

import pytest

from aieos.ai_gateway import PromptPackage, PromptPackageCatalog


def package(version: str, rollback: str) -> PromptPackage:
    return PromptPackage(
        "package",
        version,
        "Prompt Pipeline",
        "StructuredTaskKindClassification",
        "1",
        "system-v1",
        "schema-v1",
        "evaluation-v1",
        rollback,
        Decimal("0.95"),
        256,
        16,
        Decimal("0.01"),
    )


def test_exact_version_resolution_and_rollback_are_deterministic() -> None:
    first = package("v1", "v1")
    second = package("v2", "v1")
    catalog = PromptPackageCatalog((first, second))

    assert catalog.resolve("package", "v2") is second
    assert catalog.rollback(second) is first


def test_unknown_and_duplicate_packages_fail_closed() -> None:
    first = package("v1", "v1")
    with pytest.raises(ValueError, match="duplicate"):
        PromptPackageCatalog((first, first))
    with pytest.raises(LookupError):
        PromptPackageCatalog((first,)).resolve("package", "unknown")


def test_invalid_package_bounds_fail_closed() -> None:
    with pytest.raises(ValueError, match="bounds"):
        PromptPackage(
            "package",
            "v1",
            "Prompt Pipeline",
            "StructuredTaskKindClassification",
            "1",
            "system-v1",
            "schema-v1",
            "evaluation-v1",
            "v1",
            Decimal("0.95"),
            0,
            16,
            Decimal("0.01"),
        )
