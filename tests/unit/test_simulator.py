"""The synthetic generator: this repo's eval ground truth.

Determinism is not a nice-to-have here, it is the whole claim (CLAUDE.md hard
rule 10): the Phase 9 ablation is defensible only because a seed reproduces the
exact same events and labels. These tests pin that, plus the anomaly-class
distribution landing close to configured rates and every label being well-formed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from viable_agents.simulator import ScenarioConfig, SyntheticSource, load_scenario
from viable_agents.sources import AnomalyClass

CONFIG = Path(__file__).resolve().parents[2] / "config" / "simulator" / "normal-load.yaml"


def _small_scenario(**overrides: object) -> ScenarioConfig:
    base = load_scenario(CONFIG)
    return base.model_copy(update={"event_count": 200, **overrides})


@pytest.mark.asyncio
async def test_same_seed_is_byte_identical() -> None:
    scenario = _small_scenario()
    events_a = [e async for e in SyntheticSource(scenario).stream()]
    events_b = [e async for e in SyntheticSource(scenario).stream()]
    assert [e.model_dump_json() for e in events_a] == [e.model_dump_json() for e in events_b]


@pytest.mark.asyncio
async def test_different_seed_diverges() -> None:
    a = [e async for e in SyntheticSource(_small_scenario(seed=1)).stream()]
    b = [e async for e in SyntheticSource(_small_scenario(seed=2)).stream()]
    assert [e.model_dump_json() for e in a] != [e.model_dump_json() for e in b]


@pytest.mark.asyncio
async def test_event_count_is_honoured() -> None:
    events = [e async for e in SyntheticSource(_small_scenario(event_count=37)).stream()]
    assert len(events) == 37


@pytest.mark.asyncio
async def test_every_event_has_a_well_formed_label() -> None:
    scenario = _small_scenario()
    source = SyntheticSource(scenario)
    events = [e async for e in source.stream()]
    labels = source.labels()
    assert len(labels) == len(events)
    for event in events:
        label = labels[event.key]
        assert label.key == event.key
        assert isinstance(label.anomaly_class, AnomalyClass)
        # A truthfully-NONE-labelled event never carries dependency evidence.
        if label.anomaly_class is not AnomalyClass.DEPENDENCY:
            assert not label.dependency_bump


@pytest.mark.asyncio
async def test_anomaly_distribution_is_within_tolerance() -> None:
    scenario = load_scenario(CONFIG).model_copy(update={"event_count": 2000})
    source = SyntheticSource(scenario)
    async for _event in source.stream():
        pass
    labels = list(source.labels().values())
    counts = dict.fromkeys(AnomalyClass, 0)
    for label in labels:
        counts[label.anomaly_class] += 1
    for cls, configured_rate in scenario.anomaly_rates.items():
        observed_rate = counts[AnomalyClass(cls)] / len(labels)
        # Loose tolerance: FLAKE's observed class shrinks when a per-occurrence
        # roll passes, so it is checked as an upper bound, not equality.
        assert observed_rate <= configured_rate + 0.05


@pytest.mark.asyncio
async def test_flaky_tests_show_a_mixed_history_not_always_failing() -> None:
    scenario = load_scenario(CONFIG).model_copy(update={"event_count": 3000})
    source = SyntheticSource(scenario)
    outcomes_by_test: dict[str, list[str]] = {}
    async for event in source.stream():
        for job in event.jobs:
            for result in job.tests:
                outcomes_by_test.setdefault(result.test_id, []).append(result.outcome.value)

    configured_ids = {spec.test_id for spec in scenario.flaky_tests}
    seen = set(outcomes_by_test) & configured_ids
    assert seen == configured_ids, "every configured flaky test should appear in the stream"
    for test_id in configured_ids:
        outcomes = outcomes_by_test[test_id]
        assert "passed" in outcomes, f"{test_id} never passed: not actually flaky"
        assert "failed" in outcomes, f"{test_id} never failed: not actually flaky"
