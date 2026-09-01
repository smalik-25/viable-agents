"""Langfuse tracer, selected at runtime and lazy-imported.

``langfuse`` is imported only when keys are present, so the OpenTelemetry stack it
pulls never loads in CI. Every operation is wrapped so a tracing error degrades to
a no-op rather than failing the run. Span attributes carry ``run_id``,
``envelope_id``, ``channel``, ``intent``, ``sender`` and ``recipient`` so the
POSIWID auditor can reconstruct topology from traces; large values (log excerpts)
go in observation metadata, never in propagated attributes, which the v4 SDK caps
at 200 characters and silently truncates.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from viable_agents.kernel.tracing import NullTracer, Span, Tracer

_DISABLED_VALUES = {"false", "0", "no"}


def build_tracer() -> Tracer:
    if os.environ.get("LANGFUSE_TRACING_ENABLED", "true").strip().lower() in _DISABLED_VALUES:
        return NullTracer()
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return NullTracer()
    try:
        return LangfuseTracer()
    except Exception:
        return NullTracer()


class _LangfuseSpan:
    def __init__(self, observation: Any) -> None:
        self._obs = observation

    def update(self, **fields: Any) -> None:
        with contextlib.suppress(Exception):
            self._obs.update(**fields)


class LangfuseTracer:
    """Best-effort wrapper over the Langfuse v4 client."""

    def __init__(self) -> None:
        from langfuse import get_client  # noqa: PLC0415 - lazy: keeps OTel out of CI

        self._client = get_client()

    @contextmanager
    def turn(self, *, name: str, attrs: Mapping[str, str]) -> Iterator[Span]:
        try:
            cm = self._client.start_as_current_observation(as_type="span", name=name)
        except Exception:
            yield _NoOp()
            return
        with cm as observation:
            with contextlib.suppress(Exception):
                observation.update(metadata=dict(attrs))
            yield _LangfuseSpan(observation)

    @contextmanager
    def generation(self, *, name: str, model: str, attrs: Mapping[str, str]) -> Iterator[Span]:
        try:
            cm = self._client.start_as_current_observation(
                as_type="generation", name=name, model=model
            )
        except Exception:
            yield _NoOp()
            return
        with cm as observation:
            with contextlib.suppress(Exception):
                observation.update(metadata=dict(attrs))
            yield _LangfuseSpan(observation)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self._client.flush()


class _NoOp:
    def update(self, **fields: Any) -> None:
        return None
