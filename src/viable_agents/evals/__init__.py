"""The eval harness: scenario suite, ablation runner, report generator.

The proof artifact. Configurations under test differ only in config, never in
code: a flat orchestrator, VSM minus algedonic, VSM minus S3*, and full VSM, over
the same seeds and the same budgets. Metrics per run include task success rate,
dollars, tokens, wall time and sim time, time-to-detection per injected failure,
escalation precision and recall, human interruptions, and anomaly-catch rate.

The findings section is written honestly, including wherever VSM overhead costs
more than it returns. A component that does not pay for itself stays in the
ablation and gets said so. Phase 9.
"""
