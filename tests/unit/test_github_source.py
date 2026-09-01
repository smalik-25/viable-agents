"""The read-only GitHub adapter: never a write verb, honours the cache.

CLAUDE.md hard rule 5: no writes, ever. This is asserted two ways: statically
(the module's source contains no ``.post(``, ``.patch(``, ``.put(`` or
``.delete(`` call against the httpx client) and dynamically (a mock transport
that raises on any non-GET method, which every other test in this file also uses
as its transport, so a write slipping into a new code path would fail loudly).
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from viable_agents.sources import GitHubSource, InMemoryGitHubCache, WatchlistConfig
from viable_agents.sources.github import GitHubRateLimitError

_TEST_TOKEN = "test-token-not-a-real-credential"  # noqa: S105 - fixture value, not a secret

_RUN = {
    "id": 1,
    "run_attempt": 1,
    "name": "ci",
    "conclusion": "failure",
    "head_branch": "main",
    "head_sha": "a" * 40,
    "actor": {"login": "alice"},
    "head_commit": {"message": "fix: bug"},
    "created_at": "2026-01-01T00:00:00Z",
    "run_started_at": "2026-01-01T00:00:01Z",
    "updated_at": "2026-01-01T00:05:00Z",
}
_JOBS = {
    "jobs": [
        {
            "name": "build",
            "conclusion": "failure",
            "started_at": "2026-01-01T00:00:01Z",
            "completed_at": "2026-01-01T00:05:00Z",
            "steps": [{"name": "run tests", "conclusion": "failure"}],
        }
    ]
}
_COMMIT = {"files": [{"filename": "src/foo.py"}]}


def _watchlist(**overrides: object) -> WatchlistConfig:
    fields: dict[str, object] = {
        "poll_interval_seconds": 300,
        "max_runs_per_repo_per_poll": 10,
        "repos": ["octo/widgets"],
    }
    fields.update(overrides)
    return WatchlistConfig.model_validate(fields)


def test_module_source_issues_no_http_write_verb() -> None:
    """The local cache's own ``.put()`` is not an HTTP call; only ``self._client.*``
    reaches GitHub, so only that receiver is checked for a write-verb method."""
    src = Path("src/viable_agents/sources/github.py").read_text(encoding="utf-8")
    for verb in ("post(", "patch(", "put(", "delete("):
        assert f"self._client.{verb}" not in src, f"a client.{verb!r} call was found"


def _handler_factory(
    *, runs: dict[str, object] | None = None, etag: str | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            msg = f"non-GET request issued: {request.method} {request.url}"
            raise AssertionError(msg)
        path = request.url.path
        if path.endswith("/actions/runs"):
            headers = {"ETag": etag} if etag else {}
            return httpx.Response(200, json=runs or {"workflow_runs": [_RUN]}, headers=headers)
        if path.endswith("/jobs"):
            return httpx.Response(200, json=_JOBS)
        if re.search(r"/commits/", path):
            return httpx.Response(200, json=_COMMIT)
        msg = f"unexpected path {path}"
        raise AssertionError(msg)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_stream_yields_events_and_fetches_files_only_for_failures() -> None:
    client = httpx.AsyncClient(transport=_handler_factory(), base_url="https://api.github.com")
    source = GitHubSource(
        watchlist=_watchlist(), token=_TEST_TOKEN, cache=InMemoryGitHubCache(), client=client
    )
    events = [e async for e in source.stream()]
    assert len(events) == 1
    event = events[0]
    assert event.conclusion.value == "failure"
    assert event.changed_files == ("src/foo.py",)
    assert event.log_excerpts
    await source.aclose()


@pytest.mark.asyncio
async def test_passing_run_never_fetches_commit_files() -> None:
    passing_run = {**_RUN, "conclusion": "success"}
    client = httpx.AsyncClient(
        transport=_handler_factory(runs={"workflow_runs": [passing_run]}),
        base_url="https://api.github.com",
    )
    source = GitHubSource(
        watchlist=_watchlist(), token=_TEST_TOKEN, cache=InMemoryGitHubCache(), client=client
    )
    events = [e async for e in source.stream()]
    assert events[0].conclusion.value == "success"
    assert events[0].changed_files == ()
    await source.aclose()


@pytest.mark.asyncio
async def test_second_poll_sends_if_none_match_and_reuses_cached_body_on_304() -> None:
    seen_if_none_match: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            msg = "non-GET request issued"
            raise AssertionError(msg)
        if request.url.path.endswith("/actions/runs"):
            seen_if_none_match.append(request.headers.get("if-none-match"))
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, json={"workflow_runs": [_RUN]}, headers={"ETag": '"v1"'})
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=_JOBS)
        return httpx.Response(200, json=_COMMIT)

    cache = InMemoryGitHubCache()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    source = GitHubSource(watchlist=_watchlist(), token=_TEST_TOKEN, cache=cache, client=client)
    first = [e async for e in source.stream()]
    second = [e async for e in source.stream()]
    await source.aclose()

    assert len(first) == len(second) == 1
    assert seen_if_none_match == [None, '"v1"']


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_raises_a_typed_error_not_a_silent_sleep() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            msg = "non-GET request issued"
            raise AssertionError(msg)
        return httpx.Response(
            403,
            json={"message": "rate limited"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4102444800"},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    source = GitHubSource(
        watchlist=_watchlist(), token=_TEST_TOKEN, cache=InMemoryGitHubCache(), client=client
    )
    with pytest.raises(GitHubRateLimitError):
        async for _event in source.stream():
            pass
    await source.aclose()
