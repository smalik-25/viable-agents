"""Observability: the tracer selection. Langfuse is a viewer, never the record.

``build_tracer`` returns the kernel's ``NullTracer`` whenever keys are absent or
tracing is disabled, which is how CI and eval runs operate, and a ``LangfuseTracer``
otherwise. Every tracing operation is best-effort: a tracing failure never takes
down a run, because Postgres already holds the data.
"""

from viable_agents.observability.tracer import build_tracer

__all__ = ["build_tracer"]
