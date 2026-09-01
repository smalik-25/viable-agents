"""Recursion-safe addressing.

Addresses are paths, not names, or recursion is precluded on day one. A future
sub-VSM inside an S1 addresses as one more path segment with no change to the wire
format. ``resolve_metasystem`` is the single choice most likely to preclude
recursion if gotten wrong: it resolves the algedonic target as the metasystem of
the sender's enclosing recursion, which at one level is identical to hardcoding
S5 but at two levels is Cybersyn's actual escalation rule.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from viable_agents.kernel.channels import Channel, Role


class AgentAddress(BaseModel):
    """Path-shaped identity, root segment first.

    ``("fleet", "build_triage_0")`` is a level-1 unit under the level-0
    metasystem. Its own future interior addresses as
    ``("fleet", "build_triage_0", "log_fetcher_0")``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Annotated[tuple[str, ...], Field(min_length=1)]
    role: Role

    @model_validator(mode="after")
    def _check_segments(self) -> Self:
        for seg in self.path:
            if not seg or "/" in seg:
                msg = f"invalid path segment {seg!r}"
                raise ValueError(msg)
        return self

    @property
    def level(self) -> int:
        """Recursion depth. Derived, never stored."""
        return len(self.path) - 1

    @property
    def parent(self) -> tuple[str, ...]:
        return self.path[:-1]

    def canonical(self) -> str:
        return "/".join(self.path)

    def is_sibling_of(self, other: AgentAddress) -> bool:
        return self.parent == other.parent and self.path != other.path

    def is_parent_of(self, other: AgentAddress) -> bool:
        return other.path[:-1] == self.path

    def __str__(self) -> str:
        return f"{self.canonical()}[{self.role.value}]"


class RoleAddress(BaseModel):
    """A seat rather than an instance. ALGEDONIC targets the S5 seat, not a PolicyAgent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Annotated[tuple[str, ...], Field(min_length=1)]
    role: Role

    def __str__(self) -> str:
        return f"{'/'.join(self.scope)}[{self.role.value}]"


Recipient = AgentAddress | RoleAddress


def resolve_metasystem(sender: AgentAddress) -> RoleAddress:
    """The algedonic target: the metasystem of the sender's enclosing recursion.

    At level 1 this returns the level-0 S5 seat, which is what "any to S5" means
    with one recursion level. Phase 6's escalation ladder increments a level and
    re-resolves against the parent scope, rather than special-casing S5.
    """
    scope = sender.parent if sender.level > 0 else sender.path
    return RoleAddress(scope=scope, role=Role.S5)


def is_addressable(sender: AgentAddress, recipient: Recipient, channel: Channel) -> bool:
    """Beer's closure: a unit's interior is opaque to the level above it.

    ALGEDONIC and ENVIRONMENT are exempt by definition; the bypass exists
    precisely to traverse recursion levels, and the environment is shared.
    """
    if channel in (Channel.ALGEDONIC, Channel.ENVIRONMENT):
        return True
    scope = recipient.path if isinstance(recipient, AgentAddress) else recipient.scope
    return (
        scope[:-1] == sender.parent  # sibling
        or scope == sender.parent  # parent metasystem
        or scope[:-1] == sender.path  # direct child
    )
