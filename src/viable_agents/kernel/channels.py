"""Channels, roles, and intents: the vocabulary the routing matrix is written in.

Beer's First Axiom of Management counts six vertical components of corporate
cohesion, plus the algedonic bypass as a separate thing. This kernel uses five
channels and recovers what the collapse loses with an `Intent` discriminator.
Every collapse is named in `docs/ARCHITECTURE.md` rather than left implicit,
because an undeclared collapse is a confound in the Phase 9 ablation.

Nothing here decides who may talk to whom. That is data, and it lives in
`config/topology/*.yaml` (Phase 1).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Channel", "Intent", "Role"]


class Channel(StrEnum):
    """The five channels. One of them, ALGEDONIC, is the bypass.

    COMMAND collapses Beer's V1 (corporate intervention, downward only, which
    spends S1 autonomy every time it is used) and V2 (the resource bargain and
    its accountability return path, bidirectional and closed-loop). The `Intent`
    discriminator is what tells them apart, which matters because counting
    interventions separately from allocations is the autonomy-erosion metric the
    POSIWID auditor wants.

    COORDINATION collapses V3 (System 2 anti-oscillation damping) and V5 (direct
    operation-to-operation interconnection, Beer's "squiggly lines"). Sender role
    distinguishes them: sender S2 means damping, anything else is lateral.

    AUDIT carries V4, the sporadic S3* channel. Note that the audit READ is a
    database query, not a message, so a sample request is emitted as an envelope
    anyway. Otherwise the sporadic schedule is invisible and S3* becomes the one
    agent whose behavior cannot be audited.

    ALGEDONIC is the bypass. It is not a synonym for "alert": ordinary alerts
    travel the normal channels, and an algedonic signal fires only after the
    escalation timer expires unresolved.

    ENVIRONMENT collapses V6 and the horizontal S1-to-environment loops together
    with S4's total-environment scan. Beer separates S1's local-and-now
    environment from S4's total-and-future one; those are different varieties
    riding one channel here.
    """

    COMMAND = "command"
    COORDINATION = "coordination"
    AUDIT = "audit"
    ALGEDONIC = "algedonic"
    ENVIRONMENT = "environment"


class Role(StrEnum):
    """Who a participant is, for routing purposes.

    Closed so that mypy exhaustiveness checks work on match statements over it.
    DISPATCHER and WORKER exist for the Phase 9 flat-orchestrator arm, which has
    a different agent population rather than merely different edges. No VSM code
    branches on them; without them the "flat" arm would be the VSM role set with
    permissive routing, which is a different experiment.
    """

    S1 = "s1"
    S2 = "s2"
    S3 = "s3"
    S3STAR = "s3star"
    S4 = "s4"
    S5 = "s5"
    HUMAN = "human"
    ENVIRONMENT = "environment"
    DISPATCHER = "dispatcher"
    WORKER = "worker"


class Intent(StrEnum):
    """What a message is for, within its channel.

    Which intents are legal on which channel is declared in the topology config,
    not here, so the Phase 9 ablation stays a config change. This enum only fixes
    the vocabulary.
    """

    # COMMAND. `intervention`, `allocation` and `policy` travel downward only.
    # `accountability` is the upward half of Beer's resource bargain, which is
    # why "an S1 must not send on COMMAND" is scoped by intent rather than
    # applied to the whole channel: an S1 reporting on resources it was given is
    # the loop closing, not an S1 issuing orders.
    POLICY = "policy"
    ALLOCATION = "allocation"
    INTERVENTION = "intervention"
    ACCOUNTABILITY = "accountability"
    PAUSE = "pause"
    RESUME = "resume"

    # COORDINATION
    CLAIM = "claim"
    RELEASE = "release"
    ARBITRATE = "arbitrate"
    LATERAL = "lateral"
    HOMEOSTAT = "homeostat"

    # AUDIT
    SAMPLE_REQUEST = "sample_request"
    FINDING = "finding"
    CHARTER_DIFF = "charter_diff"

    # ALGEDONIC. Beer's algedonic signal carries pleasure as well as pain.
    PAIN = "pain"
    PLEASURE = "pleasure"
    ACK = "ack"
    RESOLVE = "resolve"

    # ENVIRONMENT
    OBSERVATION = "observation"
    FORECAST = "forecast"
