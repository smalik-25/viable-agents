"""The algedonic bus: the pain and pleasure channel that bypasses the hierarchy.

Algedonic is not a synonym for "alert". An ordinary alert travels the normal
channels. An algedonic signal fires only after the owning unit's supervisor has
had a defined interval to resolve it and has not, at which point it goes straight
to S5 and to the human, skipping every level in between.

Detectors are code, not models: cost runaway, failure storm, latency cliff,
triage-consistency drop, quota burn, retry cascade, and silence. Thresholds and
escalation intervals live in `config/algedonic.yaml`. Phase 6.
"""
