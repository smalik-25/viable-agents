# DEVLOG

Running log of what changed and why. Newest first.

## 2026-08-31 - Phase 2: CI event sources and the three S1 workers

Real work happening: a seeded synthetic CI simulator that doubles as eval ground
truth, a read-only GitHub live adapter, and three S1 agents (`BuildTriageAgent`,
`FlakeAgent`, `DepAgent`) that cannot tell which source an event came from
(hard rule 4). `uv run viable-agents run --source synthetic --events 500 --verify`
is the phase's self-verifying entry point, the way `demo --verify` gates v0.1: it
streams 500 seeded events through the fleet and checks that triage verdicts and
flake/dependency reports were produced, no agent degraded, and (synthetic mode
only) `BuildTriageAgent`'s verdicts agree with the ground-truth labels at or above
70%. Measured agreement on `config/simulator/normal-load.yaml` is 100% against
the free scripted classifier, which is closer to "the heuristics were written to
match the generator's own text" than a claim about a real model's accuracy; the
number that will mean something is the one from a `--live-llm` pass.

What's new, by seam:

- **`CIEvent` is the one shape both sources produce** (`sources/events.py`), a
  `Payload` so it rides an envelope and rehydrates through the existing registry
  with no kernel change. The ground-truth `TruthLabel` is deliberately NOT on the
  event: it lives in a side manifest the synthetic source keeps, so an S1 can
  never read the answer off the question it was handed.
- **The synthetic generator's flakiness is a discovered pattern, not a scripted
  one.** A configured flaky test rolls its own `flake_rate` on every appearance
  (`random.Random(seed)`, never the module-level `random`), so the same test
  passes most of the time and fails at roughly its configured rate across the
  whole stream. `FlakeAgent` is pure code, no model tier, for the same reason S2
  will be: flakiness is a threshold over an observed distribution, and a Haiku
  call could not decide it any better than the arithmetic does.
- **The GitHub adapter has exactly one method capable of an HTTP request**
  (`GitHubSource._get`), and it only ever issues a GET; every response is cached
  by URL (`github_cache`, not run-scoped: the point is to spend rate limit once
  per URL, forever) with conditional `If-None-Match` re-fetches. Rate-limit
  exhaustion raises `GitHubRateLimitError` with the reset time rather than
  sleeping for an unbounded span. `tests/unit/test_github_source.py` asserts the
  no-write claim twice: statically (no `self._client.{post,patch,put,delete}(`
  in the module) and dynamically (a mock transport that raises on any non-GET).
- **Retry-on-invalid, closing a hard-rule-6 gap from Phase 1.** Neither
  `ScriptedLLMClient` nor `AnthropicLLMClient` actually retried a structured
  output that failed schema validation; both raised immediately. Both now retry
  once (`MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2`) and record a `llm_calls` row for
  every attempt, failed or not, before raising `StructuredOutputError` if every
  attempt failed. This is Phase 1 infrastructure, not Phase 2 scope by the
  letter of PLAN.md, but Phase 2 is the first phase where a structured decision
  runs in anger, and it is cheaper to close the gap at the source than to write
  a test against a feature that does not exist.
- **The fleet roster is config, not a hardcoded address list.** `run.py` reads
  `config/fleet/vsm.yaml`'s `agent_type` field and builds the matching class
  from a small registry; a fourth S1 worker type is a config addition plus a
  registry entry, not a rewrite of the run loop. `config/fleet/vsm.yaml` now
  lists `flake_0` and `dep_0` alongside `build_triage_0`.
- **Structured decisions are free and deterministic by default.** The scripted
  responders (`systems/s1/scripting.py`) read the same JSON context a real
  Haiku call would; `--live-llm` swaps in `AnthropicLLMClient` per run. Nothing
  in CI or the default test suite spends against the $50 project ceiling.

Watchlist (`config/sources/watchlist.yaml`): eight Python-ecosystem repos chosen
for GitHub-Actions-hosted (not self-hosted) runners, permissive licenses, and
workflow-run volume high enough to have genuine flaky-test history --
`pandas-dev/pandas`, `numpy/numpy`, `scikit-learn/scikit-learn`,
`python-poetry/poetry`, `pytest-dev/pytest`, `urllib3/urllib3`,
`sqlalchemy/sqlalchemy`, `encode/httpx`.

Housekeeping: `httpx` moves from a transitive dependency (via `anthropic`) to an
explicit one, since `sources/github.py` now imports it directly. New migration
`0002_github_cache` (explicit `op.create_table`, not `create_all`; only the
initial revision uses that shortcut).

Not yet verified:

- The live watchlist pass and the real spot-check against hand-labeled runs both
  need a minted, read-only `GITHUB_TOKEN` and `ANTHROPIC_API_KEY`; neither is set
  yet. `uv run viable-agents run --source live --live-llm --verify` is that run
  when the keys exist.
- Environmental notes for whoever runs this next, both pre-existing and neither
  a Phase 2 regression: `uv run pytest` and even plain file reads under `.venv`
  intermittently stalled for minutes on this machine during this session, traced
  to Docker Desktop's virtiofs share over `/Users` colliding with Time Machine's
  `backupd-helper`, both starting around the same time; `.venv/bin/python -m
  pytest` directly (bypassing `uv run`) was unaffected once both settled. And
  the Anaconda-base editable-install flake CLAUDE.md already documents recurred:
  `viable_agents.pth` existed with the correct path but `site.py` was not
  applying it, so `uv run viable-agents ...` raised `ModuleNotFoundError` right
  after the identical invocation had worked; `uv sync --reinstall-package
  viable-agents` fixed it, as it did in Phase 1. Full suite: 584 passed, and
  `uv run viable-agents run --source synthetic --events 200 --verify` passes
  through the real entry point.

## 2026-08-31 - Phase 1 hardening: an ORM flush-ordering bug in the persistence layer

`test_envelope_row_persists_with_payload` (a new `RunRow` and a new `MessageRow`
committed in one session) intermittently raised a Postgres foreign-key violation.
Cause: SQLAlchemy's unit-of-work only orders inserts across mapper classes when a
`relationship()` gives it a dependency edge; without one it falls back to
alphabetical-by-table-name (`agents`, `messages`, ... before `runs`), so a child
row could insert before its parent existed. Fix: a plain `run: Mapped[RunRow] =
relationship()` on `AgentRow`, `MessageRow`, `LLMCallRow`, and
`ChannelSaturationRow`. Verified by temporarily removing the relationship and
reproducing the exact `ForeignKeyViolationError` against a real Postgres, then
restoring it and confirming the commit succeeds.

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
