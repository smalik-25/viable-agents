"""Cost arithmetic. Decimal only; a float never touches the accounting path.

Prices are effective-dated. ``price_for`` fails loudly when no row covers a date
rather than silently reusing the last one, because Sonnet 5's introductory rate
expires 2026-08-31 and a stale price retroactively poisons the Phase 9 cost
column. 5-minute and 1-hour cache writes are never collapsed: their multipliers
differ (1.25x vs 2x base input).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from viable_agents.kernel.cost import Usage

MTOK = Decimal(1_000_000)
CENT = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens, effective from a date."""

    effective_from: dt.date
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal


class NoPriceError(LookupError):
    """No pricing row covers the requested date. Never reuse the last row."""


def price_for(rows: list[Price], at: dt.date) -> Price:
    covering = [p for p in rows if p.effective_from <= at]
    if not covering:
        msg = f"no pricing row effective on {at}"
        raise NoPriceError(msg)
    return max(covering, key=lambda p: p.effective_from)


def cost_usd(usage: Usage, price: Price) -> Decimal:
    raw = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_5m_tokens * price.cache_write_5m
        + usage.cache_write_1h_tokens * price.cache_write_1h
    ) / MTOK
    return raw.quantize(CENT, rounding=ROUND_HALF_UP)
