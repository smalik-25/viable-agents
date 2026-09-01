"""The Phase 1 demo, end to end, with no database and no API key.

This exercises the whole kernel in one run: two stub agents over the bus, a
scripted priced LLM call with cost accounting, the algedonic bypass, and a
recorded topology violation. It is the same code path the ``viable-agents demo
--verify`` command runs to gate the v0.1 tag.
"""

from __future__ import annotations

import uuid

import pytest

from viable_agents.demo import _run


@pytest.mark.asyncio
async def test_demo_meets_every_exit_check() -> None:
    result = await _run(run_id=uuid.uuid4())
    failed = [label for label, passed in result.checks.items() if not passed]
    assert not failed, f"demo checks failed: {failed}"
    assert result.llm_cost > 0
