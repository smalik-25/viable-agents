"""Tracer protocol. ``kernel/`` never imports langfuse or opentelemetry.

``LangfuseTracer`` lives in ``observability/`` and is selected at runtime by the
presence of keys. When keys are absent (CI, eval runs) the ``NullTracer`` is used,
so nothing branches on ``tracer is not None`` and no ``Optional[Tracer]`` leaks
into the Agent base. Postgres is the system of record; a dropped trace never loses
data.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol


class Span(Protocol):
    def update(self, **fields: Any) -> None: ...


class Tracer(Protocol):
    @contextmanager
    def turn(self, *, name: str, attrs: Mapping[str, str]) -> Iterator[Span]: ...

    @contextmanager
    def generation(self, *, name: str, model: str, attrs: Mapping[str, str]) -> Iterator[Span]: ...

    def flush(self) -> None: ...


class _NullSpan:
    def update(self, **fields: Any) -> None:
        return None


class NullTracer:
    """No-op tracer. The default whenever Langfuse keys are absent."""

    @contextmanager
    def turn(self, *, name: str, attrs: Mapping[str, str]) -> Iterator[Span]:
        yield _NullSpan()

    @contextmanager
    def generation(self, *, name: str, model: str, attrs: Mapping[str, str]) -> Iterator[Span]:
        yield _NullSpan()

    def flush(self) -> None:
        return None
