"""The kernel: the VSM substrate, and nothing else.

This package is the one place in the repo held to `mypy --strict` and to a hard
line budget (1,500 non-blank non-comment lines, measured by `make kernel-budget`).
It contains the Envelope, the payload registry, channels/roles/intents/addresses,
the Topology matcher, the Bus, the Agent base, the Clock, and Protocol definitions
(`Tracer`, `LLMClient`, `Sink`).

It contains no I/O implementations. No PyYAML, no SQLAlchemy, no Anthropic, no
Langfuse, no LangChain, no LangGraph. Config parses YAML and hands the kernel a
compiled routing matrix; persistence lives in `db/`; cost arithmetic lives in
`llm/`. `tests/unit/test_kernel_imports.py` (Phase 1) enforces this by walking the
import graph, because the line budget alone would happily permit one 400-line
persistence module.
"""

from viable_agents.kernel.channels import Channel, Intent, Role

__all__ = ["Channel", "Intent", "Role"]
