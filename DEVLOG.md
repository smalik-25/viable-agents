# DEVLOG

Running log of what changed and why. Newest first.

## 2026-09-01 - Phase 4: S3 control, the resource bargain

`Controller` now owns the per-run budget: hard per-agent caps are enforced for
the first time (they existed as unread stubs since Phase 0), a running-pool
monitor sheds low-priority S1 work under pressure and resumes it when headroom
returns, and a periodic `RunReport` -- work done, cost, anomaly counts per
agent, plus a one-sentence narrative -- lands in Postgres. Every COMMAND rule
this needed (`cmd_s3_s1_alloc/intervene/pause/resume`, `cmd_s1_s3_account`) was
already reserved in `config/topology/vsm.yaml` since Phase 1; no topology
change landed with this phase, same as Phase 3.

**Budget enforcement is client-level, not bus-level**, correcting a claim
`kernel/bus.py`'s `add_pre_send` docstring had carried since Phase 0/1: an LLM
call is `self.llm.complete(...)`, called directly by an agent on its client,
and never crosses `Bus.send()`, so a bus seam cannot see it. `BudgetGuard`
(`llm/budget.py`) lives where the spend happens instead, checked once per
retry ATTEMPT inside `ScriptedLLMClient`/`AnthropicLLMClient`'s existing
retry-on-invalid loop, not once per `complete()` call -- a check only at the
top would let a validation retry cross the cap unrefused, since each attempt
already records its own full-cost `llm_calls` row. A refusal still writes a
`$0`, `succeeded=False`, `error="budget_exceeded"` row before raising, so a
refusal is data (hard rule 3), not only an exception. `project_ceiling_usd`
is enforced the same way, scoped run-local for now (cross-run needs a
Postgres query at run start the in-memory synthetic path cannot perform;
parked in `PLAN.md`).

**Allocation is a running-pool monitor, not a one-shot grant**, because the
shipped `starved_run_budget_usd` (0.24) exactly equals the S1 `budget_usd`
sum in `config/fleet/vsm.yaml` (0.15 + 0.09 + 0.00) -- a one-time
"grant-each-instance-its-nominal-ask" allocator would shed nobody on that
exact break-even. Instead `Controller` tracks cumulative spend (read from the
same `BudgetGuard` the LLM clients write to, one ledger, not two) against the
pool on every accountability report and pauses the lowest-priority live S1
once the pool is spent. This surfaced a real bug during testing: because
cumulative spend never refunds, comparing it against a fixed pool on every
single report re-triggered a shed on every subsequent report too, cascading
through the whole roster instead of shedding once. Fixed with an arm/disarm
flag: a shed disarms itself and only re-arms on a resume, so at most one shed
responds to each distinct overage. Caught by
`test_resume_when_a_running_agent_becomes_permanently_exhausted` before it
shipped.

**Resume fires on permanent exhaustion, not on spend "coming back down"**
(it structurally can't -- cumulative spend is monotonic). When a running
agent hits its OWN `BudgetGuard` cap, it can never spend another dollar this
run, which is the only real headroom signal available; `Controller` resumes
the highest-priority paused agent at that point. This exercises `Agent.pause()`
/`resume()` (implemented since Phase 1, never called by anything before this)
via genuine budget headroom, not an invented performance metric -- PLAN.md's
"reprioritizes when an S1 underperforms" needs a quality signal no
accountability payload carries today, so that stays out of scope and is
parked rather than guessed at.

S1 gained pause/resume dispatch it did not have at all: `S1Worker.
_handle_control` consumes COMMAND/pause and COMMAND/resume, called first by
every concrete `handle()` (`triage.py`, `flake.py`, `dep.py`). Without it
`Controller`'s directives would have silently dropped -- confirmed missing by
grepping for `PAUSE`/`RESUME` across `systems/s1/` before writing it.

`priority: int` is new on `AgentSpec`/`config/fleet/vsm.yaml`: S1 workers get
the lowest values (only they are actually shed), and the metasystem seats get
placeholder values reflecting Beer's hierarchy ahead of their own phases
instantiating them. Controller's own Sonnet-tier call is the RunReport
narrative only -- the allocation decision itself stays deterministic, the
same "code attenuates cheaper than tokens" argument Phase 3's S2 module
docstring makes, extended here because budget enforcement should not be an
LLM's call. A narrative-drafting failure (budget exceeded, invalid structured
output) falls back to an empty string rather than raising, so the agent
responsible for preventing degradation cannot degrade itself over prose.

Ran into and fixed a real circular import while wiring this up:
`persistence` (via `llm.recorder`) pulled in `persistence.run_reports`,
which imported `systems.s3.reports` for a type hint -- but importing that
submodule triggers `systems/s3/__init__.py`, which eagerly loads
`controller.py`, which imports from `llm.client`, still mid-initialization
from the top of the same chain. Fixed by moving that import under
`TYPE_CHECKING`: `RunReportRecord` only ever holds an already-constructed
`RunReport`, so the real class is never needed at runtime, only the
annotation.

A worthwhile side effect of enforcement finally being real: the DEFAULT
(non-starved) 500-event run's numbers changed from Phase 2/3's baseline (171
triage verdicts, $0.4664) to 69 verdicts and $0.2618, because `build_triage_0`'s
own `budget_usd` cap ($0.15) -- inert since Phase 0 -- now genuinely refuses
further calls once hit, independent of and before Controller's pool ever
gets involved (no agent was paused in that run; the pool, $0.60, was nowhere
near spent). Not a regression: every exit check still passes, agreement is
still 100% on what did get classified, and it is exactly what "hard per-agent
caps... an over-budget agent's LLM calls are refused" asks for. The starved
run (`--starved-budget`) separately and additionally pauses `flake_0`
(lowest priority, $0 cap) via the pool monitor.

7 new files (`llm/budget.py`, `llm/budgets_config.py`, `systems/s3/reports.py`,
`systems/s3/controller.py`, `systems/s3/scripting.py`,
`persistence/run_reports.py`, `migrations/versions/0003_run_reports.py`) plus
tests (`test_budget_guard.py`, `test_s3_controller.py`,
`tests/integration/test_run_reports.py`, a starved-budget scenario added to
`test_run.py`). Full suite: 609 passed against real Postgres, `ruff`/
`ruff format`/`mypy --strict` all clean, kernel at 1,182/1,500 lines (up 17
from `BudgetExceededError`'s new fields and a corrected docstring -- no
kernel behavior changed, only a typed error and an accurate comment).

## 2026-09-01 - Phase 3: S2 coordination, the anti-oscillation test

`BuildTriageAgent` now claims a `(run key, work type)` pair from `Coordinator`
before classifying and releases it once it reports, so two instances of the
same S1 type racing the same workflow run -- a legitimate horizontal scale-out,
since `env_in_s1` fans a CI event out to every agent registered under the S1
role, not to one instance -- do not both report it. `WorkClaimLedger`
(`systems/s2/ledger.py`) is first-claim-wins, pure code, keyed by `(run key,
work type)` so a `BuildTriageAgent` and a `FlakeAgent` on the same run never
contend, and TTL-reclaimable so a degraded S1 does not strand a run unclaimed
forever. No topology change: `coord_s1_s2_claim`, `coord_s2_s1_arbitrate` and
`coord_s1_s2_release` were already reserved in `config/topology/vsm.yaml` since
Phase 1, and `coordinator_0` was already in `config/fleet/vsm.yaml`.

The claim is fire-and-forget, not a blocking round trip: an agent's inbox is a
single FIFO queue drained one envelope per `handle()` call, so blocking mid-turn
for a reply would stall on whatever arrives after it or corrupt delivery order.
`S1Worker.claim()` stashes the causing envelope and returns; the `ARBITRATE`
reply is a later, independent `handle()` call that resumes the stashed work if
granted. This is the design's one real behavior change: `BuildTriageAgent` now
defaults to `coordinate=True`, so its existing Phase 2 tests needed updating to
drive the real two-hop protocol (`tests/unit/test_s1_agents.py`'s new `_process`
helper) rather than expecting a report from a single `handle()` call.
`coordinate=False` reproduces Phase 2's original, unmediated behavior and exists
so the thrash test can demonstrate the failure using this exact class, not a
stand-in. `FlakeAgent` and `DepAgent` are unchanged: the fleet has exactly one
instance of each today, so there is no actual contention for them to damp yet;
wiring them in later is additive, not a redesign.

Per hard rule 8, `tests/unit/test_s2_coordination.py`'s first test
(`test_two_build_triage_agents_thrash_without_coordination`) was written and
run against `coordinate=False` before `Coordinator` existed, showing the bug:
two accountability envelopes for one workflow run. `docs/ARCHITECTURE.md`
already flagged the real risk with this channel's collapse -- a test built
around client-side coordination could pass because the S1s "sorted it out
themselves" rather than because S2 damped anything -- so every coordinated test
routes the claim through a real `Coordinator.handle()` call and inspects its
`decisions` directly. Exit criteria: the thrash test fails with coordination off
and passes with it on; `test_duplicate_claim_rate_is_zero_across_a_thousand_runs`
runs 1,000 distinct workflow runs through two competing `BuildTriageAgent`s with
`Coordinator` wired in and asserts zero keys were ever granted to more than one
owner. 7 new tests, plus 4 pure-code `WorkClaimLedger` tests (grant, deny,
release, TTL reclaim, cross-work-type independence) needing no bus at all.

Caught and fixed before this landed: the 1,000-run test's winning claimant
releases its claim every iteration, and the first version of the test never
drained that release from S2's inbox. COORDINATION's queue is bounded
(`maxsize: 256`, `overflow: block`), so the queue saturated around iteration
256 and every release past that blocked for `block_timeout_seconds` before
raising `ChannelSaturatedError` -- the test just hung. `run.py`'s real pipeline
never hits this: `Coordinator` runs as a persistent agent task that drains its
own inbox continuously, unlike the test's manual, one-message-at-a-time
stepping. Fixed by draining the release each iteration; confirmed the fixed
test runs in under a second.

`Coordinator` is now wired into `run.py`'s standard fleet build (`_AGENT_TYPES`,
`_build_agents`), so `viable-agents run --source synthetic --events 500
--verify` genuinely goes through S2 in production, not only in tests. Verified
the numbers are unchanged from Phase 2's baseline (171 triage verdicts, 3 flake
assessments, 41 dep summaries, 100% agreement, $0.4664 LLM cost) -- expected,
since the default fleet has exactly one `BuildTriageAgent` and no contention;
the coordination round trip adds latency, not a behavior change, when nothing
is actually racing. Full suite: 591 passed against real Postgres (584 from
Phase 2 plus 7 new), `ruff`/`ruff format`/`mypy --strict` all clean, kernel
untouched (1,165 / 1,500 lines) since none of this needed a kernel change.

## 2026-09-01 - Phase 2 hardening: migration 0001 was silently absorbing later tables

CI failed `alembic upgrade head` from an empty database with
`DuplicateTableError: relation "github_cache" already exists`. Cause: migration
0001's `Base.metadata.create_all(bind=op.get_bind())` was unqualified, and
`Base.metadata` is a live registry of every model currently imported, not a
snapshot of what existed when the revision was written. Once `GitHubCacheRow`
was added to `models.py` for 0002, 0001's `create_all()` started creating
`github_cache` too, and 0002's own explicit `op.create_table("github_cache",
...)` then collided with it on any fresh database. Fix: 0001 now passes
`tables=[Base.metadata.tables[name] for name in _TABLES]` naming exactly the
five original tables (`runs`, `agents`, `messages`, `llm_calls`,
`channel_saturation`), so its output is fixed regardless of what gets added to
`models.py` later. Verified by dropping to a genuinely empty database
(`docker exec ... createdb migration_check`) and re-running `alembic upgrade
head` clean, then the full suite: 584 passed against real Postgres, including
`test_persistence.py`'s `compare_metadata` drift check and
`test_github_cache.py`.

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
