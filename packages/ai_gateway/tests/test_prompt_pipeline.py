"""Prompt Pipeline governed package construction and release tests."""

from dataclasses import replace
from decimal import Decimal

import pytest

from aieos.ai_gateway import PackageState, PromptPackage, PromptPackageCatalog


def package(version: str, *, state: PackageState, rollback: str | None) -> PromptPackage:
    return PromptPackage(
        reference="structured-task-kind",
        version_reference=version,
        owner="Prompt Pipeline",
        capability_id="StructuredTaskKindClassification",
        capability_contract_version_id="1",
        typed_variables=(("statement", "string[1..512]"),),
        system_instruction_reference=f"structured-task-kind-system-{version}",
        system_instruction="Classify only the communicative form.",
        output_schema_reference="structured-task-kind-schema-v1",
        output_schema={
            "type": "object",
            "properties": {
                "task_kind": {
                    "type": "string",
                    "enum": ["Question", "Instruction", "Statement"],
                }
            },
            "required": ["task_kind"],
            "additionalProperties": False,
        },
        task_class="classification",
        evaluation_set_reference=f"structured-task-kind-protected-{version}",
        rollback_version_reference=rollback,
        quality_threshold=Decimal("0.95"),
        per_class_recall_threshold=Decimal("0.90"),
        max_regression=Decimal("0.02"),
        max_input_tokens=256,
        max_output_tokens=16,
        max_cost=Decimal("0.01"),
        state=state,
        change_history=(f"{version} test fixture",),
    )


STRUCTURED_TASK_KIND_PACKAGE = package("v1", state=PackageState.CANDIDATE, rollback=None)


def catalog() -> PromptPackageCatalog:
    return PromptPackageCatalog((STRUCTURED_TASK_KIND_PACKAGE,))


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


def test_package_schema_is_deeply_immutable() -> None:
    schema = STRUCTURED_TASK_KIND_PACKAGE.output_schema
    properties = schema["properties"]
    assert isinstance(properties, dict | type(schema))
    with pytest.raises(TypeError):
        properties["other"] = {}  # type: ignore[index]
    enum = properties["task_kind"]["enum"]  # type: ignore[index]
    assert isinstance(enum, tuple)
    with pytest.raises(TypeError):
        enum[0] = "Other"  # type: ignore[index]


def test_disabled_unknown_and_incompatible_packages_fail_closed() -> None:
    disabled = replace(STRUCTURED_TASK_KIND_PACKAGE, state=PackageState.DISABLED)
    packages = PromptPackageCatalog((disabled,))
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


def test_first_release_failure_retains_inactive_state_without_fabricated_target() -> None:
    packages = catalog()
    recall = {kind: Decimal("0.96") for kind in ("Question", "Instruction", "Statement")}
    assert (
        packages.release_selection(
            STRUCTURED_TASK_KIND_PACKAGE,
            accuracy=Decimal("0.97"),
            per_class_recall=recall,
            rollback_accuracy=None,
            safety_and_schema_passed=True,
        )
        is STRUCTURED_TASK_KIND_PACKAGE
    )
    assert (
        packages.release_selection(
            STRUCTURED_TASK_KIND_PACKAGE,
            accuracy=Decimal("0.94"),
            per_class_recall=recall,
            rollback_accuracy=None,
            safety_and_schema_passed=True,
        )
        is None
    )


def test_later_release_requires_genuinely_approved_rollback_target() -> None:
    approved = package("v1", state=PackageState.APPROVED, rollback=None)
    candidate = package("v2", state=PackageState.CANDIDATE, rollback="v1")
    packages = PromptPackageCatalog((approved, candidate))
    recall = {kind: Decimal("0.96") for kind in ("Question", "Instruction", "Statement")}

    assert (
        packages.release_selection(
            candidate,
            accuracy=Decimal("0.94"),
            per_class_recall=recall,
            rollback_accuracy=Decimal("0.98"),
            safety_and_schema_passed=True,
        )
        is approved
    )
    with pytest.raises(LookupError, match="first-release"):
        packages.rollback(replace(candidate, rollback_version_reference=None))

    missing = replace(candidate, rollback_version_reference=None)
    missing_catalog = PromptPackageCatalog((approved, missing))
    with pytest.raises(LookupError, match="first-release"):
        missing_catalog.release_selection(
            missing,
            accuracy=Decimal("0.99"),
            per_class_recall=recall,
            rollback_accuracy=Decimal("0.98"),
            safety_and_schema_passed=True,
        )
    with pytest.raises(ValueError, match="exact governed class set"):
        packages.release_selection(
            candidate,
            accuracy=Decimal("0.99"),
            per_class_recall={"Question": Decimal("0.99")},
            rollback_accuracy=Decimal("0.98"),
            safety_and_schema_passed=True,
        )
