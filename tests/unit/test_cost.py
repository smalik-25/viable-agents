"""Cost accounting is exact, and the Sonnet price rollover is pinned by a test.

A dated price is exactly the kind of fact that rots silently and retroactively
poisons the Phase 9 cost column, so the 2026-08-31 introductory-rate expiry is
asserted on both sides, and an uncovered date fails loudly rather than reusing the
last row.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from viable_agents.kernel import Usage
from viable_agents.llm import NoPriceError, cost_usd, load_models, price_for

MODELS = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


def test_cost_is_computed_per_mtok_in_decimal() -> None:
    config = load_models(MODELS)
    haiku = config.spec("haiku")
    price = price_for(haiku.prices(), dt.date(2026, 7, 1))
    # 1,000,000 input tokens at $1/MTok = exactly $1.00
    assert cost_usd(Usage(input_tokens=1_000_000), price) == Decimal("1.00000000")
    # 200,000 output tokens at $5/MTok = $1.00
    assert cost_usd(Usage(output_tokens=200_000), price) == Decimal("1.00000000")


def test_cache_reads_are_a_tenth_of_input() -> None:
    config = load_models(MODELS)
    price = price_for(config.spec("haiku").prices(), dt.date(2026, 7, 1))
    assert cost_usd(Usage(cache_read_tokens=1_000_000), price) == Decimal("0.10000000")


def test_sonnet_introductory_price_expires_2026_08_31() -> None:
    config = load_models(MODELS)
    rows = config.spec("sonnet").prices()
    assert price_for(rows, dt.date(2026, 8, 31)).input == Decimal("2.00")
    assert price_for(rows, dt.date(2026, 9, 1)).input == Decimal("3.00")


def test_uncovered_date_fails_loudly() -> None:
    config = load_models(MODELS)
    rows = config.spec("sonnet").prices()
    with pytest.raises(NoPriceError):
        price_for(rows, dt.date(2025, 1, 1))
