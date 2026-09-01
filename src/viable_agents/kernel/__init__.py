"""The kernel: the VSM substrate, and nothing else.

Held to ``mypy --strict`` (via 12 explicit flags in pyproject) and to a hard line
budget measured by ``make kernel-budget``. Contains the Envelope, the payload
registry, channels/roles/intents/addresses, the Topology matcher, the Bus, the
Agent base, the Clock, cost arithmetic, and Protocol definitions (``Tracer``,
``LLMClient``, ``Sink``). No I/O implementations: no SQLAlchemy, Anthropic,
Langfuse, LangChain, LangGraph, or PyYAML-backed config loading. ``config/`` hands
the kernel a compiled routing matrix; ``persistence/`` implements ``Sink``;
``llm/`` implements ``LLMClient``. ``tests/unit/test_kernel_imports.py`` enforces
this by walking the import graph.
"""

from viable_agents.kernel.address import (
    AgentAddress,
    Recipient,
    RoleAddress,
    is_addressable,
    resolve_metasystem,
)
from viable_agents.kernel.agent import Agent, AgentState
from viable_agents.kernel.bus import Bus, DeliveryStatus, Sink
from viable_agents.kernel.channels import Channel, Intent, Role
from viable_agents.kernel.clock import Clock, RealClock, VirtualClock
from viable_agents.kernel.cost import Usage
from viable_agents.kernel.envelope import (
    Citation,
    Envelope,
    EscalationState,
    MetricRef,
)
from viable_agents.kernel.errors import (
    BudgetExceededError,
    BusClosedError,
    ChannelSaturatedError,
    ClosureViolationError,
    KernelError,
    TopologyViolationError,
    UnknownRecipientError,
)
from viable_agents.kernel.llm import LLMClient, LLMResult
from viable_agents.kernel.payload import Payload, PayloadKindError
from viable_agents.kernel.topology import (
    ChannelSpec,
    Decision,
    OverflowPolicy,
    Rule,
    Topology,
)
from viable_agents.kernel.tracing import NullTracer, Span, Tracer

__all__ = [
    "Agent",
    "AgentAddress",
    "AgentState",
    "BudgetExceededError",
    "Bus",
    "BusClosedError",
    "Channel",
    "ChannelSaturatedError",
    "ChannelSpec",
    "Citation",
    "Clock",
    "ClosureViolationError",
    "Decision",
    "DeliveryStatus",
    "Envelope",
    "EscalationState",
    "Intent",
    "KernelError",
    "LLMClient",
    "LLMResult",
    "MetricRef",
    "NullTracer",
    "OverflowPolicy",
    "Payload",
    "PayloadKindError",
    "RealClock",
    "Recipient",
    "Role",
    "RoleAddress",
    "Rule",
    "Sink",
    "Span",
    "Topology",
    "TopologyViolationError",
    "Tracer",
    "UnknownRecipientError",
    "Usage",
    "VirtualClock",
    "is_addressable",
    "resolve_metasystem",
]
