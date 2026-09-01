"""The routing matrix, loaded from ``config/topology/*.yaml``.

No VSM rule lives in Python. This is the research object: the flat and full-VSM
arms of the Phase 9 ablation are two files loaded into this one class. Two
properties make that honest. The matrix is TOTAL: every (sender, recipient,
channel) cell resolves to a named rule, never an implicit default, because an
absent deny is not visible in a config diff. And authorization is scoped by
INTENT: a rule may permit S1 to send ``accountability`` on COMMAND (Beer's V2
resource bargain closing its loop) while the catch-all still forbids S1 from
sending ``intervention``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from viable_agents.kernel.channels import Channel, Intent, Role
from viable_agents.kernel.errors import TopologyViolationError

DEFAULT_RULE_ID = "__default__"
CHANNEL_DISABLED_ID = "__channel_disabled__"
INTENT_INVALID_ID = "__intent_invalid__"


class OverflowPolicy(StrEnum):
    """Beer's Principle 2 (channel capacity) made a config value."""

    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    REJECT = "reject"


class ChannelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    maxsize: int = Field(default=256, ge=0)
    overflow: OverflowPolicy = OverflowPolicy.BLOCK
    block_timeout_seconds: float = Field(default=5.0, gt=0)
    allowed_intents: frozenset[Intent]


class Rule(BaseModel):
    """One row of the matrix. ``*`` is the only wildcard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    sender: Role | Literal["*"]
    recipient: Role | Literal["*"]
    channel: Channel | Literal["*"]
    intent: Intent | Literal["*"] = "*"
    level_delta: int | Literal["*"] = "*"
    effect: Literal["allow", "deny"]
    reason: str = ""

    def specificity(self) -> tuple[bool, bool, bool, bool, bool]:
        return (
            self.sender != "*",
            self.recipient != "*",
            self.channel != "*",
            self.intent != "*",
            self.level_delta != "*",
        )

    def matches(
        self,
        sender: Role,
        recipient: Role,
        channel: Channel,
        intent: Intent | None,
        level_delta: int,
    ) -> bool:
        # intent None is a channel-level query (coverage, rendering): the intent
        # dimension is treated as a match so the cell resolves to a named rule.
        intent_ok = intent is None or self.intent in ("*", intent)
        return (
            self.sender in ("*", sender)
            and self.recipient in ("*", recipient)
            and self.channel in ("*", channel)
            and intent_ok
            and self.level_delta in ("*", level_delta)
        )


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    rule_id: str
    reason: str


class Topology(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int = 1
    default_effect: Literal["allow", "deny"] = "deny"
    channels: Mapping[Channel, ChannelSpec]
    rules: Sequence[Rule]

    @model_validator(mode="after")
    def _validate(self) -> Self:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            msg = f"topology {self.name!r} has duplicate rule ids: {dupes}"
            raise ValueError(msg)
        for rule in self.rules:
            if rule.intent != "*" and rule.channel != "*":
                spec = self.channels.get(rule.channel)
                if spec is not None and rule.intent not in spec.allowed_intents:
                    msg = (
                        f"rule {rule.id!r} cites intent {rule.intent.value!r} "
                        f"not declared on channel {rule.channel.value!r}"
                    )
                    raise ValueError(msg)
        uncovered = list(self._uncovered())
        if uncovered:
            head = ", ".join(f"{s.value}->{r.value} on {c.value}" for s, r, c in uncovered[:5])
            msg = (
                f"topology {self.name!r} is not total: {len(uncovered)} "
                f"uncovered cells (e.g. {head})"
            )
            raise ValueError(msg)
        return self

    def _uncovered(self) -> Iterator[tuple[Role, Role, Channel]]:
        for sender in Role:
            for recipient in Role:
                for channel in self.channels:
                    if self._best(sender, recipient, channel, None) is None:
                        yield (sender, recipient, channel)

    def _best(
        self,
        sender: Role,
        recipient: Role,
        channel: Channel,
        intent: Intent | None,
        level_delta: int = 0,
    ) -> Rule | None:
        """Most specific rule wins; ties break by declaration order (earliest)."""
        best: Rule | None = None
        best_key: tuple[tuple[bool, ...], int] | None = None
        for i, rule in enumerate(self.rules):
            if not rule.matches(sender, recipient, channel, intent, level_delta):
                continue
            key = (rule.specificity(), -i)
            if best_key is None or key > best_key:
                best_key, best = key, rule
        return best

    def decide(
        self,
        sender: Role,
        recipient: Role,
        channel: Channel,
        intent: Intent | None = None,
        level_delta: int = 0,
    ) -> Decision:
        spec = self.channels.get(channel)
        if intent is not None:
            if spec is None or not spec.enabled:
                return Decision(
                    allowed=False,
                    rule_id=CHANNEL_DISABLED_ID,
                    reason=f"channel {channel.value} disabled in {self.name!r}",
                )
            if intent not in spec.allowed_intents:
                return Decision(
                    allowed=False,
                    rule_id=INTENT_INVALID_ID,
                    reason=f"intent {intent.value} not valid on {channel.value}",
                )
        rule = self._best(sender, recipient, channel, intent, level_delta)
        if rule is None:
            return Decision(
                allowed=self.default_effect == "allow",
                rule_id=DEFAULT_RULE_ID,
                reason=f"no rule matched; default {self.default_effect}",
            )
        return Decision(
            allowed=rule.effect == "allow",
            rule_id=rule.id,
            reason=rule.reason or f"{rule.id} {rule.effect}",
        )

    def is_allowed(
        self,
        sender: Role,
        recipient: Role,
        channel: Channel,
        intent: Intent | None = None,
    ) -> bool:
        return self.decide(sender, recipient, channel, intent).allowed

    def check(
        self,
        sender: Role,
        recipient: Role,
        channel: Channel,
        intent: Intent | None = None,
    ) -> Decision:
        """Return the decision if allowed; raise TopologyViolationError if not."""
        decision = self.decide(sender, recipient, channel, intent)
        if not decision.allowed:
            raise TopologyViolationError(
                sender=sender,
                recipient=recipient,
                channel=channel,
                intent=intent,
                reason=decision.reason,
                rule_id=decision.rule_id,
            )
        return decision

    def render(self) -> str:
        """Markdown table, diffed against a golden file so topology edits are reviewable."""
        lines = [
            f"# Routing matrix: {self.name} (v{self.version})",
            "",
            f"Default effect: `{self.default_effect}`. "
            f"{len(self.rules)} rules over {len(self.channels)} channels.",
            "",
            "| id | sender | recipient | channel | intent | effect | reason |",
            "|----|--------|-----------|---------|--------|--------|--------|",
        ]
        for rule in self.rules:
            intent = rule.intent if rule.intent == "*" else rule.intent.value
            channel = rule.channel if rule.channel == "*" else rule.channel.value
            sender = rule.sender if rule.sender == "*" else rule.sender.value
            recipient = rule.recipient if rule.recipient == "*" else rule.recipient.value
            lines.append(
                f"| {rule.id} | {sender} | {recipient} | {channel} | "
                f"{intent} | {rule.effect} | {rule.reason} |"
            )
        return "\n".join(lines) + "\n"
