"""Live CI event sources.

A read-only GitHub adapter over workflow runs, job logs and check annotations for
a configurable watchlist. Read-only is a hard rule, not a default: no PRs, no
issues, no comments. Responses cache to Postgres and rate limits are respected,
which later becomes an S2 and S3 concern on purpose.

Everything downstream of `CIEvent` must be unable to tell live from synthetic.
Phase 2.
"""
