# viable-agents. Requires uv and Docker.
# Targets that spend money say so.

.PHONY: sync up down lint fmt typecheck test gate kernel-budget

sync:
	uv sync

# Postgres 16 on host port 5433.
up:
	docker compose up -d

down:
	docker compose down

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy

# Never touches a live API: the `live` marker is deselected.
test:
	uv run pytest -m "not live"

# Everything CI runs, in the same order.
gate: lint typecheck
	uv run ruff format --check --diff .
	uv run pytest -m "not live"

# CLAUDE.md hard rule 2 caps the kernel at 1,500 non-blank non-comment lines.
# An unmeasured constraint is one that gets quietly violated.
kernel-budget:
	@n=$$(find src/viable_agents/kernel -name '*.py' -exec cat {} + 2>/dev/null \
		| grep -cve '^[[:space:]]*$$' -e '^[[:space:]]*#'); \
	echo "kernel: $$n / 1500 lines"; \
	if [ $$n -ge 1500 ]; then echo "OVER BUDGET"; exit 1; fi; \
	if [ $$n -ge 1200 ]; then echo "warning: past the 1200-line warning line"; fi
