# DEVLOG

Running log of what changed and why. Newest first.

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
