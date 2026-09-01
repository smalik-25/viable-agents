"""Load ``config/budgets.yaml`` into a typed config.

Mirrors ``llm/config.py``'s ``load_models`` exactly: the kernel never reads
YAML (hard rule 2), so this compile step lives outside it and hands the
resolved numbers to whatever needs them (``BudgetGuard`` for per-agent caps,
``Controller`` for the run pool). ``cost_runaway`` is modeled here too even
though nothing reads it until Phase 6's algedonic detector, so this file stays
a faithful, total representation of the config it loads rather than a subset
picked to match today's callers.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class CostRunawayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    k: Decimal
    window_events: int
    warmup_events: int


class BudgetsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_run_budget_usd: Decimal
    per_agent_caps_usd: dict[str, Decimal]
    starved_run_budget_usd: Decimal
    cost_runaway: CostRunawayConfig
    project_ceiling_usd: Decimal


def load_budgets(path: Path) -> BudgetsConfig:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a mapping at top level"
        raise TypeError(msg)
    return BudgetsConfig.model_validate(raw)
