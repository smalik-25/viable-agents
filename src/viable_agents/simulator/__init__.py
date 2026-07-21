"""The seeded synthetic CI event generator.

Produces workflow runs with logs, test results, durations and commit metadata,
with ground-truth anomalies injected at known rates: real breakages, flaky tests
with known flake rates, infra outages, dependency-induced failures. Deterministic
given a seed.

This is the eval ground truth, which is why it is not an afterthought: the
Phase 9 ablation is only defensible because the labels come from here rather than
from a model's opinion. Failure injection (`--inject cost_bomb|tool_outage|drift`)
carries a ground-truth onset timestamp so time-to-detection is measurable.
Phase 2, extended in Phase 6.
"""
