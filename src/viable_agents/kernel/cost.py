"""The token-usage record: the five counts an LLM call reports.

This is the part of the cost contract the kernel needs, because ``LLMResult``
carries it. The pricing table and the arithmetic that turns usage into dollars
live in ``llm/pricing.py`` outside the kernel, since they depend on
``config/models.yaml`` and the kernel imports no config. Prompt caching makes
input tokens non-uniform in price, so the counts are kept separate and 5-minute
and 1-hour cache writes are never collapsed (their multipliers differ).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0

    @property
    def total_prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )
