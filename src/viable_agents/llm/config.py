"""Load ``config/models.yaml`` into typed tier specs.

Model IDs live only in that file (hard rule 3). A tier spec carries capability
flags, not just an id, because the tiers are not request-compatible: Sonnet 5
requires adaptive thinking and rejects non-default sampling params, Haiku 4.5
requires ``budget_tokens`` and rejects ``effort``. A single shared request-kwargs
dict would 400 the first time an S1 and an S3 run in the same process, which is
exactly the Phase 1 exit criterion.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from viable_agents.llm.pricing import Price


class PriceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_from: dt.date
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_5m_per_mtok: Decimal
    cache_write_1h_per_mtok: Decimal
    cache_read_per_mtok: Decimal

    def to_price(self) -> Price:
        return Price(
            effective_from=self.effective_from,
            input=self.input_per_mtok,
            output=self.output_per_mtok,
            cache_read=self.cache_read_per_mtok,
            cache_write_5m=self.cache_write_5m_per_mtok,
            cache_write_1h=self.cache_write_1h_per_mtok,
        )


class TierSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    max_context_tokens: int
    max_output_tokens: int
    default_max_tokens: int
    thinking_mode: str
    supports_effort: bool
    supports_sampling_params: bool
    pricing: list[PriceRow] = Field(min_length=1)

    def prices(self) -> list[Price]:
        return [row.to_price() for row in self.pricing]


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tiers: dict[str, TierSpec]
    role_tiers: dict[str, str]
    overlays: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def tier_for_role(self, role: str, *, overlay: str | None = None) -> str | None:
        if overlay and overlay in self.overlays:
            overridden = self.overlays[overlay].get("role_tiers", {})
            if role in overridden:
                tier = overridden[role]
                return None if tier == "none" else str(tier)
        tier = self.role_tiers.get(role)
        return None if tier in (None, "none") else str(tier)

    def spec(self, tier: str) -> TierSpec:
        return self.tiers[tier]


def load_models(path: Path) -> ModelsConfig:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a mapping at top level"
        raise TypeError(msg)
    return ModelsConfig.model_validate(raw)
