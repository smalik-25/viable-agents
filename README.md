# viable-agents

A multi-agent LLM orchestrator structured as Stafford Beer's Viable System Model,
with an algedonic escalation bypass and a POSIWID auditor built in.

Most multi-agent frameworks give you a graph and let you draw whatever edges you
like. This one takes the position that the interesting object is the
communication topology itself: which system may say what to which other system,
on which channel, and what happens when a signal has to skip the hierarchy
entirely. Five systems, five channels, one of which is the bypass. The topology
lives in YAML, not in code branches, so the same binary runs a flat orchestrator
and a full VSM and the difference between them is a diff you can read.

Demo domain: a CI/CD engineering fleet operating on GitHub workflow runs. S1
agents triage failing builds, classify flaky tests, and summarise dependency
bumps, against either live public repositories (read-only) or a seeded simulator
with ground-truth failure labels.

Status: Phase 4. The kernel runs (a data-driven routing matrix, 500 cells total,
39 differing between the flat and full-VSM arms; a bus that enforces whichever
topology it is handed; path-shaped recursion-safe addresses; a dual clock;
Postgres persistence behind Alembic migrations), and real work happens on top of
it: a seeded synthetic CI simulator that doubles as eval ground truth, a
read-only GitHub live adapter, and three S1 agents (build triage, flake
detection, dependency-bump summaries) that cannot tell which source an event
came from. `BuildTriageAgent` now claims a run through S2's `Coordinator`
before classifying and releases it once it reports, so two instances of the
same worker type racing the same workflow run do not both report it -- a
work-claim ledger, pure code, no model call. `Controller` (S3) enforces hard
per-agent spend caps, sheds low-priority S1 work and resumes it when a
starved run's budget pool is under pressure, and emits a periodic `RunReport`
to Postgres. `uv run viable-agents run --source synthetic --events 500
--verify` is self-checking the way the Phase 1 demo is (`--starved-budget`
demonstrates graceful shedding). The kernel is 1,182 lines, `mypy --strict`
clean, and imports nothing heavier than Pydantic.

## Why this and not a graph framework

- **The bypass.** An algedonic signal is not a high-severity alert. It is a signal
  that the unit which owned the problem was given a defined interval to fix it and
  did not, at which point it goes straight to policy and to a human, skipping every
  level between. That escalation ladder is the thing hierarchies structurally
  cannot do, and it is measurable: time-to-detection against a known injected
  onset.
- **The auditor.** S3* samples complete traces on a schedule the agents cannot
  anticipate, infers what an agent's purpose actually is from what it actually
  did, and diffs that against its written charter. The seeded misbehaviour case is
  a triage agent that labels hard failures "flaky" and recommends retries, because
  that keeps its resolution count up. Goodhart's law with a name and a test.
- **The ablation.** Flat orchestrator, VSM minus algedonic, VSM minus S3*, full
  VSM. Same models, same budgets, same seeds, differing only in configuration.
  If a component does not pay for itself, the table says so and it stays in the
  table.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the diagram, the channel
table, and an honest account of where this implementation collapses six of Beer's
vertical channels into five.

| Component | Module | Model tier |
|---|---|---|
| S1 Operations | [systems/s1](src/viable_agents/systems/s1) | Haiku-class (FlakeAgent: none, pure code) |
| S2 Coordination | [systems/s2](src/viable_agents/systems/s2) | none, pure code |
| S3 Control | [systems/s3](src/viable_agents/systems/s3) | Sonnet-class |
| S3* Audit | [posiwid](src/viable_agents/posiwid) | Sonnet-class |
| S4 Intelligence | [systems/s4](src/viable_agents/systems/s4) | Sonnet-class |
| S5 Policy | [systems/s5](src/viable_agents/systems/s5) | Sonnet-class + human |
| Algedonic bus | [algedonic](src/viable_agents/algedonic) | detectors are code |
| CI sources | [sources](src/viable_agents/sources), [simulator](src/viable_agents/simulator) | n/a (read-only adapter, seeded generator) |
| Kernel | [kernel](src/viable_agents/kernel) | n/a |

Model tiering is Ashby's Law used as a routing table rather than as decoration:
S1 classifies against a fixed taxonomy and gets a cheap model, the metasystem
arbitrates and gets a stronger one, and S2 attenuates variety in code and gets no
model at all. Phase 9 runs the flat arm twice, once all-cheap and once with a
strong dispatcher, so a reader can tell the topology effect from the tiering
effect.

## Quickstart

```bash
git clone https://github.com/smalik-25/viable-agents && cd viable-agents
uv sync
make gate                        # ruff, mypy, pytest
uv run viable-agents demo --verify   # two agents over the bus, self-checked
uv run viable-agents run --source synthetic --events 500 --verify  # the S1 fleet
uv run viable-agents run --source synthetic --events 500 --starved-budget --verify  # S3 sheds

docker compose up -d             # Postgres 16 on port 5433
uv run alembic upgrade head      # build the schema
uv run pytest                    # includes the Postgres integration tests
```

`run --source live` needs a read-only `GITHUB_TOKEN`; `--live-llm` needs
`ANTHROPIC_API_KEY` and spends against the project's $50 ceiling. Neither is
required for the default synthetic, scripted-classifier path above, which is
free and deterministic.

## Roadmap

- [x] Phase 0: repo bootstrap, CI, architecture stub
- [x] Phase 1: kernel (envelope, bus, agent base, routing matrix, persistence)
- [x] Phase 2: CI event sources, three S1 worker types
- [x] Phase 3: S2 coordination, the anti-oscillation test
- [x] Phase 4: S3 control, budget allocation
- [ ] Phase 5: S5 policy, charter, human channel
- [ ] Phase 6: algedonic bus, failure injection, time-to-detection
- [ ] Phase 7: POSIWID auditor, judge calibration
- [ ] Phase 8: S4 intelligence, forecasting, the S3/S4 homeostat
- [ ] Phase 9: eval harness and the ablation table
- [ ] Phase 10: MCP surface, docs, launch

## Lineage

Beer, *Brain of the Firm* (1972) and *The Heart of Enterprise* (1979). Medina,
*Cybernetic Revolutionaries* (2011), which is also the honest account of how much
thinner Cybersyn's operational reality was than its myth; this project implements
the design intent, not the legend. See also arXiv:2503.00237 (Miehling et al.,
*Agentic AI Needs a Systems Theory*).

## License

MIT
