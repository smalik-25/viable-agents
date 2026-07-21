"""S1, Operations: the units that do the object-level work.

Every S1 is in principle a viable system in its own right. That recursion is not
implemented, but no interface here may preclude it, which is why addresses are
path-shaped and the algedonic recipient is resolved rather than hardcoded.

Phase 2 adds BuildTriageAgent (classify a failing workflow run as regression,
infra, flake or dependency, citing a log excerpt), FlakeAgent (per-test pass/fail
history), and DepAgent (dependency-bump impact summaries). Haiku tier.
"""
