"""S3*, the POSIWID auditor: the purpose of a system is what it does.

Samples complete traces on a stochastic schedule (sporadic by design, not a cron
the agents can anticipate), reconstructs what an agent actually did, infers its
de facto purpose, and diffs that against its stated charter. Findings travel on
AUDIT to S3; a finding above the severity threshold also fires ALGEDONIC, because
an agent doing something other than its stated job is pain.

Every finding cites envelope ids and trace ids. An uncited finding is a bug.
Scope is deliberately narrow: it audits agents in this fleet and nothing else.
Phase 7.
"""
