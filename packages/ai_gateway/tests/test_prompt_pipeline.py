"""Prompt Pipeline governed package construction and release tests."""

from dataclasses import replace
from decimal import Decimal

import pytest

from aieos.ai_gateway import PackageState, PromptPackageCatalog
from aieos.skill_runtime import (
    STRUCTURED_TASK_KIND_PACKAGE,
    STRUCTURED_TASK_KIND_ROLLBACK_PACKAGE,
)


def catalog() -> PromptPackageCatalog:
    return PromptPackageCatalog(
        (STRUCTURED_TASK_KIND_ROLLBACK_PACKAGE, STRUCTURED_TASK_KIND_PACKAGE)
    )


def test_binding_and_assembly_are_exact_deterministic_and_context_free() -> None:
    packages = catalog()
    first = packages.assemble("structured-task-kind", "v1", {"statement": " Status? "})
    second = packages.assemble("structured-task-kind", "v1", {"statement": "Status?"})

    assert first == second
    assert first.package_identity == STRUCTURED_TASK_KIND_PACKAGE.identity
    assert "<task class='classification'>\nStatus?\n</task>" in first.content
    assert "<schema ref='structured-task-kind-schema-v1'>" in first.content
    assert "history" not in first.content.lower()
    assert "context" not in first.content.lower()


def test_construction_is_model_free_and_validates_exact_variables() -> None:
    packages = catalog()
    with pytest.raises(ValueError, match="variables"):
        packages.assemble("structured-task-kind", "v1", {"statement": "ok", "extra": "x"})


def test_disabled_unknown_and_incompatible_packages_fail_closed() -> None:
    disabled = replace(STRUCTURED_TASK_KIND_PACKAGE, state=PackageState.DISABLED)
    packages = PromptPackageCatalog((STRUCTURED_TASK_KIND_ROLLBACK_PACKAGE, disabled))
    with pytest.raises(LookupError):
        packages.resolve("structured-task-kind", "v1")
    with pytest.raises(LookupError):
        packages.resolve("structured-task-kind", "unknown")
    with pytest.raises(ValueError, match="duplicate"):
        PromptPackageCatalog((disabled, disabled))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("quality_threshold", Decimal("1.01"), "quality threshold"),
        ("per_class_recall_threshold", Decimal("-0.01"), "per-class"),
        ("max_regression", Decimal("1.01"), "maximum regression"),
        ("max_input_tokens", 255, "bounds"),
        ("max_output_tokens", 17, "bounds"),
        ("max_cost", Decimal("0.02"), "bounds"),
        ("capability_contract_version_id", "2", "contract"),
    ],
)
def test_governed_invariants_fail_closed(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        replace(STRUCTURED_TASK_KIND_PACKAGE, **{field: value})


def test_release_selection_enforces_threshold_recall_and_regression_rollback() -> None:
    packages = catalog()
    recall = {kind: Decimal("0.96") for kind in ("Question", "Instruction", "Statement")}
    assert (
        packages.release_selection(
            STRUCTURED_TASK_KIND_PACKAGE,
            accuracy=Decimal("0.97"),
            per_class_recall=recall,
            rollback_accuracy=Decimal("0.98"),
            safety_and_schema_passed=True,
        )
        is STRUCTURED_TASK_KIND_PACKAGE
    )
    assert (
        packages.release_selection(
            STRUCTURED_TASK_KIND_PACKAGE,
            accuracy=Decimal("0.94"),
            per_class_recall=recall,
            rollback_accuracy=Decimal("0.98"),
            safety_and_schema_passed=True,
        )
        is STRUCTURED_TASK_KIND_ROLLBACK_PACKAGE
    )
