"""The read-only GitHub live adapter (CLAUDE.md hard rule 5: no writes, ever).

``_get`` is the *only* method in this module that issues an HTTP request, and it
only ever issues a GET. There is no code path here capable of a POST, PATCH, PUT
or DELETE against the GitHub API: ``tests/unit/test_github_source.py`` asserts
this both by static inspection of this module's source and by replaying a mock
transport that raises on any non-GET verb.

Every response is cached (``GitHubCache``) keyed by URL, conditionally re-fetched
with ``If-None-Match``, so a re-run or a rate-limit backoff reads local rows
instead of spending quota. Rate-limit handling is deliberately naive here (surface
a clear error with the reset time; do not silently sleep for an unbounded span) --
turning that into adaptive backoff is an S2/S3 concern in later phases, per PLAN.

Only completed runs are ever yielded (GitHub's own in-progress states have no
``conclusion`` and do not fit ``CIEvent``). Commit file lists are fetched only for
failing runs, since a passing run never reaches a triage agent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import httpx

from viable_agents.sources.cache import GitHubCache
from viable_agents.sources.config import WatchlistConfig
from viable_agents.sources.events import (
    CIEvent,
    Conclusion,
    EventKind,
    JobResult,
    LogExcerpt,
    SourceMode,
)

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_FAILING_CONCLUSIONS = {"failure", "timed_out", "cancelled"}

_CONCLUSION_MAP: dict[str, Conclusion] = {
    "success": Conclusion.SUCCESS,
    "failure": Conclusion.FAILURE,
    "cancelled": Conclusion.CANCELLED,
    "timed_out": Conclusion.TIMED_OUT,
    "skipped": Conclusion.SKIPPED,
    # GitHub states this schema has no dedicated member for. Mapping them to
    # SKIPPED is a conservative default: none of them indicate a code-caused
    # failure a triage agent should classify.
    "neutral": Conclusion.SKIPPED,
    "action_required": Conclusion.SKIPPED,
    "stale": Conclusion.SKIPPED,
}


class GitHubRateLimitError(RuntimeError):
    """Raised instead of sleeping for an unbounded span. See module docstring."""

    def __init__(self, reset_at: dt.datetime) -> None:
        self.reset_at = reset_at
        super().__init__(f"GitHub rate limit exhausted; resets at {reset_at.isoformat()}")


def _parse_dt(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def _map_conclusion(raw: str | None) -> Conclusion:
    if raw is None:
        return Conclusion.SKIPPED
    return _CONCLUSION_MAP.get(raw, Conclusion.SKIPPED)


class GitHubSource:
    """Implements ``CIEventSource`` (``sources.events.CIEventSource``)."""

    mode = SourceMode.LIVE

    def __init__(
        self,
        *,
        watchlist: WatchlistConfig,
        token: str,
        cache: GitHubCache,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._watchlist = watchlist
        self._cache = cache
        self._client = client or httpx.AsyncClient(
            base_url=_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """The sole request path: always GET, always cached, never a write verb."""
        full_url = str(httpx.URL(url, params=params))
        cached = await self._cache.get(full_url)
        headers = {"If-None-Match": cached.etag} if cached and cached.etag else {}

        response = await self._client.get(url, params=params, headers=headers)

        if response.status_code == httpx.codes.NOT_MODIFIED and cached is not None:
            return cached.body

        if response.status_code == httpx.codes.FORBIDDEN:
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset = response.headers.get("X-RateLimit-Reset")
            if remaining == "0" and reset is not None:
                reset_at = dt.datetime.fromtimestamp(int(reset), tz=dt.UTC)
                raise GitHubRateLimitError(reset_at)

        response.raise_for_status()
        body: dict[str, Any] = response.json()
        await self._cache.put(
            full_url,
            etag=response.headers.get("ETag"),
            status_code=response.status_code,
            body=body,
        )
        return body

    async def _fetch_jobs(self, repo: str, run_id: int) -> list[dict[str, Any]]:
        body = await self._get(f"/repos/{repo}/actions/runs/{run_id}/jobs")
        jobs: list[dict[str, Any]] = body.get("jobs", [])
        return jobs

    async def _fetch_changed_files(self, repo: str, sha: str) -> tuple[str, ...]:
        body = await self._get(f"/repos/{repo}/commits/{sha}")
        files: list[dict[str, Any]] = body.get("files", [])
        return tuple(f["filename"] for f in files if "filename" in f)

    def _job_result(self, raw: dict[str, Any]) -> tuple[JobResult, LogExcerpt | None]:
        conclusion = _map_conclusion(raw.get("conclusion"))
        steps: list[dict[str, Any]] = raw.get("steps") or []
        failed_step = next((s["name"] for s in steps if s.get("conclusion") == "failure"), None)
        excerpt = None
        if conclusion in (Conclusion.FAILURE, Conclusion.TIMED_OUT, Conclusion.CANCELLED):
            step_desc = f"step {failed_step!r}" if failed_step else "an unspecified step"
            excerpt = LogExcerpt(
                path=str(raw.get("name", "job")),
                start_line=0,
                end_line=0,
                text=f"{step_desc} failed in job {raw.get('name', '?')!r}",
            )
        job = JobResult(
            name=str(raw.get("name", "job")),
            conclusion=conclusion,
            started_at=_parse_dt(raw["started_at"]),
            completed_at=_parse_dt(raw.get("completed_at") or raw["started_at"]),
            failed_step=failed_step,
            tests=(),  # GitHub's Checks API does not expose per-test results generically
        )
        return job, excerpt

    async def _to_event(self, repo: str, run: dict[str, Any]) -> CIEvent:
        conclusion = _map_conclusion(run.get("conclusion"))
        run_id = int(run["id"])
        jobs_raw = await self._fetch_jobs(repo, run_id)
        jobs: list[JobResult] = []
        excerpts: list[LogExcerpt] = []
        for raw_job in jobs_raw:
            job, excerpt = self._job_result(raw_job)
            jobs.append(job)
            if excerpt is not None:
                excerpts.append(excerpt)

        head_sha = str(run["head_sha"])
        changed_files: tuple[str, ...] = ()
        if run.get("conclusion") in _FAILING_CONCLUSIONS:
            changed_files = await self._fetch_changed_files(repo, head_sha)

        head_commit = run.get("head_commit") or {}
        created_at = _parse_dt(run["created_at"])
        started_at = _parse_dt(run.get("run_started_at", run["created_at"]))
        completed_at = _parse_dt(run.get("updated_at", run["created_at"]))

        return CIEvent(
            source_mode=SourceMode.LIVE,
            event_kind=EventKind.WORKFLOW_RUN,
            repo=repo,
            run_id=run_id,
            run_attempt=int(run.get("run_attempt", 1)),
            workflow_name=str(run.get("name") or run.get("workflow_id", "workflow")),
            conclusion=conclusion,
            head_branch=str(run.get("head_branch", "")),
            head_sha=head_sha,
            actor=str((run.get("actor") or {}).get("login", "unknown")),
            commit_message=str(head_commit.get("message", "")),
            changed_files=changed_files,
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=max((completed_at - started_at).total_seconds(), 0.0),
            jobs=tuple(jobs),
            log_excerpts=tuple(excerpts),
        )

    async def stream(self) -> AsyncIterator[CIEvent]:
        for repo in self._watchlist.repos:
            body = await self._get(
                f"/repos/{repo}/actions/runs",
                params={
                    "per_page": min(self._watchlist.max_runs_per_repo_per_poll, 100),
                    "status": "completed",
                },
            )
            runs: list[dict[str, Any]] = body.get("workflow_runs", [])
            for run in runs[: self._watchlist.max_runs_per_repo_per_poll]:
                yield await self._to_event(repo, run)
