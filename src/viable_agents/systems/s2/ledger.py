"""The work-claim ledger: S2's anti-oscillation core, pure code and no I/O.

PLAN Phase 3: "``Coordinator``: mostly pure code (locks, work-claim ledger,
backoff schedules)." ``WorkClaimLedger`` is that pure code, factored out of
``Coordinator`` so it is testable with plain values and an injected timestamp,
no bus, no agent, no event loop. First-claim-wins, one owner per ``(key,
work_type)``: two ``BuildTriageAgent`` instances racing the same workflow run
contend for the same ledger key, but a ``BuildTriageAgent`` and a ``FlakeAgent``
processing the same run do not, since they carry different ``work_type``s and are
not actually duplicating each other's work.

A claim older than ``ttl_seconds`` with no matching release is reclaimable: the
"backoff schedule" PLAN.md's Phase 3 section asks for, so a degraded S1 that dies
mid-run does not strand a workflow run claimed-but-unprocessed forever.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from viable_agents.kernel.payload import Payload

# (repo, run_id, run_attempt) -- CIEvent.key. The ledger has no notion of a
# CIEvent; it only ever sees the tuple, which is what keeps this module pure.
ClaimKey = tuple[str, int, int]


class WorkClaim(Payload):
    """S1 -> S2 on COORDINATION/claim: "I want to own this unit of work."."""

    kind: str = "s2.work_claim"

    key: ClaimKey
    work_type: str


class ClaimDecision(Payload):
    """S2 -> S1 on COORDINATION/arbitrate, replying to a ``WorkClaim``.

    ``owner`` is always populated, granted or not, so a denied claimant can see
    who holds the key without a second round trip.
    """

    kind: str = "s2.claim_decision"

    key: ClaimKey
    work_type: str
    granted: bool
    owner: str


class WorkRelease(Payload):
    """S1 -> S2 on COORDINATION/release: "I'm done; free the key.\""""

    kind: str = "s2.work_release"

    key: ClaimKey
    work_type: str


@dataclass(frozen=True, slots=True)
class _Owned:
    owner: str
    claimed_at: dt.datetime


class WorkClaimLedger:
    """First-claim-wins, one owner per ``(key, work_type)``. No I/O, no LLM."""

    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._owned: dict[tuple[ClaimKey, str], _Owned] = {}

    def claim(
        self, key: ClaimKey, work_type: str, *, claimant: str, now: dt.datetime
    ) -> ClaimDecision:
        ledger_key = (key, work_type)
        existing = self._owned.get(ledger_key)
        contested = (
            existing is not None
            and existing.owner != claimant
            and not self._is_stale(existing, now)
        )
        if contested and existing is not None:
            return ClaimDecision(key=key, work_type=work_type, granted=False, owner=existing.owner)
        self._owned[ledger_key] = _Owned(owner=claimant, claimed_at=now)
        return ClaimDecision(key=key, work_type=work_type, granted=True, owner=claimant)

    def release(self, key: ClaimKey, work_type: str, *, claimant: str) -> bool:
        """Returns whether the release actually freed anything (a no-op is not an error)."""
        ledger_key = (key, work_type)
        existing = self._owned.get(ledger_key)
        if existing is None or existing.owner != claimant:
            return False
        del self._owned[ledger_key]
        return True

    def _is_stale(self, owned: _Owned, now: dt.datetime) -> bool:
        return (now - owned.claimed_at).total_seconds() > self._ttl_seconds
