"""Persistence: the tables of record, and the ``Sink`` implementations.

SQLAlchemy lives here, never in ``kernel/``. The kernel defines the ``Sink``
protocol; ``InMemorySink`` and ``PostgresSink`` implement it.
"""

from viable_agents.persistence.base import Base
from viable_agents.persistence.models import (
    AgentRow,
    ChannelSaturationRow,
    GitHubCacheRow,
    LLMCallRow,
    MessageRow,
    RunReportRow,
    RunRow,
)
from viable_agents.persistence.run_reports import (
    InMemoryRunReportRecorder,
    PostgresRunReportRecorder,
    RunReportRecord,
    RunReportRecorder,
)
from viable_agents.persistence.session import make_engine, make_session_factory
from viable_agents.persistence.sink import InMemorySink, PostgresSink, envelope_to_row

__all__ = [
    "AgentRow",
    "Base",
    "ChannelSaturationRow",
    "GitHubCacheRow",
    "InMemoryRunReportRecorder",
    "InMemorySink",
    "LLMCallRow",
    "MessageRow",
    "PostgresRunReportRecorder",
    "PostgresSink",
    "RunReportRecord",
    "RunReportRecorder",
    "RunReportRow",
    "RunRow",
    "envelope_to_row",
    "make_engine",
    "make_session_factory",
]
