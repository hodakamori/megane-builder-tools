.PHONY: sync lint lint-fix typecheck test coverage check conformance serve serve-http fixtures

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

lint-fix:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run ty check

test:
	uv run pytest

coverage:
	uv run pytest --cov-report=xml:coverage.xml

# What CI runs.
check:
	uv lock --check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) coverage
	$(MAKE) conformance

# The reference server must pass its own conformance checker, over a real stdio process.
conformance:
	uv run megane-builder-conformance -- uv run megane-builder-tools

serve:
	uv run megane-builder-tools

serve-http:
	uv run megane-builder-tools --transport http --allow-origin http://localhost:5173

# Recorded tools/list and tools/call responses for megane's client-side tests.
fixtures:
	uv run python scripts/record_fixtures.py fixtures
