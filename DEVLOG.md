# DEVLOG

Running log of what changed and why. Newest first.

## 2026-07-21 - Phase 1: the kernel, persistence, and a self-verifying demo

Built the smallest thing that is recognizably a VSM substrate. The kernel is 12
modules at 1,173 non-blank non-comment lines (`make kernel-budget`, ceiling 1,500,
warn 1,200), `mypy --strict` clean via the 12 explicit per-module flags, ruff
clean, and it imports nothing heavier than Pydantic. `tests/unit/test_kernel_imports.py`
enforces all three: no banned import (SQLAlchemy, Langfuse, OpenTelemetry,
Anthropic, PyYAML, LangChain, LangGraph) reaches the kernel graph, no ambient
`datetime.now`/`asyncio.sleep` lives outside `clock.py`, and the file manifest is
exact.

What the kernel carries that a generic message bus would not:

- **The topology is data and total.** `config/topology/vsm.yaml` and `flat.yaml`
  load into one `Topology` class. Every one of the 500 (sender, recipient,
  channel) cells resolves to a named rule, never an implicit default, so the
  flat-vs-VSM ablation is a diff you can read: 39 cells differ. Authorization is
  scoped by intent, which is how an S1 reports `accountability` upward on COMMAND
  while the catch-all still forbids it from sending `intervention`.
- **The routing-matrix test proves enforcement, not just the rules.** Beyond the
  four layers over the YAML (totality, reachability, a golden table, the named
  CLAUDE.md invariants), `tests/unit/test_bus.py` drives `Bus.send()` and shows it
  follows whichever topology it was handed: the same worker-to-dispatcher send is
  allowed under `flat` and raises under `vsm`. A bus that hardcoded the VSM rules
  in Python would fail that test.
- **Recursion is not precluded.** Addresses are path-shaped (`fleet/build_triage_0`)
  with a derived level, and the algedonic recipient resolves as "the metasystem of
  the sender's enclosing recursion" rather than a hardcoded S5, so Phase 6's
  escalation ladder becomes a loop, not a special case.
- **Cost lands on an `llm_calls` row, not the Envelope**, because one turn makes
  0..N model calls and emits 0..N envelopes. The `ScriptedLLMClient` returns
  priced fake responses so cost accounting runs end to end with no API key; the
  real Anthropic client is a thin wrapper reached only by the `live` test.
- **Postgres is the system of record.** Five tables (`runs`, `agents`, `messages`,
  `llm_calls`, `channel_saturation`) carry the columns that cannot be backfilled:
  `runs.seed` and `config_fingerprint`, `messages.causation_id` and
  `status`/`reject_reason`/`rule_id`, the five separate token counts. Langfuse is a
  best-effort viewer behind a Protocol; when keys are absent the tracer is a no-op
  and everything still runs, which is how CI and the eval harness operate.

The demo (`uv run viable-agents demo --verify`) starts an S1 (pure code) and an S3
(LLM) over the bus, drives a message both ways on COMMAND, fires the algedonic
bypass to the S5 seat, provokes a topology violation, and asserts all nine of
those happened, exiting non-zero if any did not.

Notes for the reviewer:

- The initial Alembic migration builds the schema from the declarative metadata
  (`create_all`) rather than rendered per-column DDL. It is exact by construction,
  so the "no pending migrations" test finds no drift; later phases use explicit
  `op` operations.
- `python-preference = "only-managed"` and the recurring editable-install rebuild
  meant the venv occasionally needed `uv sync --reinstall-package viable-agents`.

Not yet verified:

- The `live` Anthropic path and a real Langfuse trace: both are exercised only by
  a keyed local run. `uv run pytest -m live` and one `demo --verify` with keys gate
  the v0.1 tag; that run's trace URL goes here when it happens.
- Postgres integration tests run only where a database exists (CI service
  container, or local `docker compose up`); they skip cleanly otherwise.

## 2026-07-21 - Phase 0: repo bootstrap, and nine corrections to the plan before writing code

Scaffolded the repo: `uv` project on Python 3.12 with a src layout, ruff, mypy,
pytest, a two-job GitHub Actions workflow, Postgres 16 in Compose, and the empty
package tree for all five systems plus the algedonic bus, the POSIWID auditor,
the sources, the simulator and the evals harness. Every package `__init__.py`
carries the Beer concept it owns, so `CLAUDE.md`'s "where does X go" criterion is
answered from inside the code as well as from the contract.

Phase 0 ships three runtime dependencies (`pydantic`, `pydantic-settings`,
`pyyaml`) rather than the eleven the design first listed. Installing SQLAlchemy,
Alembic, Langfuse and the Anthropic SDK to typecheck an empty package costs 62
resolved packages in CI and makes the "the kernel imports nothing heavy" guard
vacuous from the first commit. They arrive in Phase 1 with the code that imports
them.

Nine findings from the planning pass changed the design before any code was
written. Four are worth recording here because they are silent failures:

**mypy's per-module `strict = true` leaks globally.** Reproduced on mypy 2.3.0:
an override naming only `viable_agents.kernel*` also strict-checked `systems/`,
two errors where one was expected. mypy's config parser sees the `strict` key,
fires the global `set_strict_flags()` callback and continues, so the flag never
reaches the per-module dict. Hard rule 2 therefore cannot be written the obvious
way; the override enumerates 12 explicit flags instead. The failure is invisible
in both directions (CI is green whether strict is scoped, leaked, or absent), so
[test_mypy_scoping.py](tests/unit/test_mypy_scoping.py) reads the shipped
`pyproject.toml`, asserts the flag list, and then runs mypy against a synthetic
two-package tree to prove exactly one error lands, in the kernel.

**Ruff's `TC` rules break Pydantic at runtime.** They move annotation-only
imports into `if TYPE_CHECKING:` blocks, and Pydantic resolves annotations at
runtime: the module still imports cleanly and the first validation raises
`PydanticUserError`. `ruff check --fix --unsafe-fixes` applies it for you. `TC` is
omitted from `lint.select` with the reason written next to it in
[pyproject.toml](pyproject.toml), because under mypy strict it buys nothing worth
that failure mode.

**A routing matrix alone cannot express the Phase 9 flat arm.** Three of the four
eval configurations differ in which agents exist and where CI events enter, not
only in which edges are legal, so hard rule 7's "the four configurations differ
only in config" was false as written. Config splits into three axes:
`config/topology/` (who may send what), `config/fleet/` (the roster plus the
ingress binding), `config/profiles/` (the composition root). `Role` gains
`DISPATCHER` and `WORKER`, giving 10 members; without them the "flat" arm is the
VSM role set with permissive routing, which is a different experiment.

**"An S1 must not send on COMMAND" is false as a blanket rule.** COMMAND collapses
Beer's V1 (intervention, downward) and V2 (the resource bargain and its
accountability return path, bidirectional). An S1 reporting on the resources it
was given is the loop closing. The `Intent` discriminator scopes the rule: S1 must
not send COMMAND with intent `intervention`, `allocation` or `policy`.
`accountability` is permitted S1 to S3, and counting `intervention` separately
from `allocation` is the autonomy-erosion metric S3* will want in Phase 7.

Also costed the project. Bottom-up, the plan as written is about $4,030 of
Anthropic API spend, of which Phase 9 is roughly $3,000: the per-sweep unit is
small ($384 for 45 scenarios x 5 arms at 100 events) and the count of sweeps
(about 11 for a solo dev, counting harness debugging, scenario rewrites and one
N=3 headline sweep) sets the bill. Sonnet thinking tokens bill at the output rate
and are about 75% of a full-VSM run's cost from only 19 activations against S1's
53. The project ceiling is set to $50 in
[config/budgets.yaml](config/budgets.yaml), which is a real constraint on Phase 9
and is reflected in the roadmap rather than discovered later.

Open questions:
- Langfuse Cloud project not yet created; `.env` has no keys. Phase 1's exit
  criterion needs one local traced run.
- `ANTHROPIC_API_KEY` not yet minted. Not needed until Phase 2.
- Phase 9's shape under the $50 ceiling is not settled: scenario count, arm
  count and repeats all move together and the choice determines whether the
  ablation is a result or a demonstration.

Not yet verified:
- Nothing in Phase 0 exercises the bus, because there is no bus yet. The
  routing-matrix test that hard rule 1 demands arrives with the topology loader
  in Phase 1.
- The CI workflow has not run on GitHub; it is green locally via `make gate`.
