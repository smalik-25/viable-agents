"""Live CI event sources.

A read-only GitHub adapter over workflow runs, job logs and check annotations for
a configurable watchlist. Read-only is a hard rule, not a default: no PRs, no
issues, no comments. Responses cache to Postgres and rate limits are respected,
which later becomes an S2 and S3 concern on purpose.

Everything downstream of `CIEvent` must be unable to tell live from synthetic.
Phase 2.
"""

from viable_agents.sources.cache import (
    CachedResponse,
    GitHubCache,
    InMemoryGitHubCache,
    PostgresGitHubCache,
)
from viable_agents.sources.config import WatchlistConfig, load_watchlist
from viable_agents.sources.events import (
    AnomalyClass,
    CIEvent,
    CIEventSource,
    Conclusion,
    EventKind,
    JobResult,
    LogExcerpt,
    SourceMode,
    TestOutcome,
    TestResult,
    TruthLabel,
)
from viable_agents.sources.github import GitHubRateLimitError, GitHubSource

__all__ = [
    "AnomalyClass",
    "CIEvent",
    "CIEventSource",
    "CachedResponse",
    "Conclusion",
    "EventKind",
    "GitHubCache",
    "GitHubRateLimitError",
    "GitHubSource",
    "InMemoryGitHubCache",
    "JobResult",
    "LogExcerpt",
    "PostgresGitHubCache",
    "SourceMode",
    "TestOutcome",
    "TestResult",
    "TruthLabel",
    "WatchlistConfig",
    "load_watchlist",
]
