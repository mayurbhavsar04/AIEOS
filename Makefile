.PHONY: bootstrap check format lint type test test-integration boundaries docs doctor run reset

bootstrap:
	./scripts/bootstrap

check:
	./scripts/check

format:
	uv run ruff format .

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run pyright

test:
	uv run pytest -m "not integration and not e2e"

test-integration:
	uv run pytest -m integration

boundaries:
	uv run python tooling/dependency_rules/check_boundaries.py

docs:
	uv run python tooling/docs_validation/validate_docs.py

doctor:
	uv run python tooling/doctor.py

run:
	uv run python -m aieos_api.main

reset:
	./scripts/reset-local
