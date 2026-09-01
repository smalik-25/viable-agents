"""Load ``config/sources/watchlist.yaml`` into a typed, validated config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class WatchlistConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    poll_interval_seconds: int = Field(gt=0)
    max_runs_per_repo_per_poll: int = Field(gt=0)
    repos: list[str] = Field(default_factory=list)


def load_watchlist(path: Path) -> WatchlistConfig:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a mapping at top level"
        raise TypeError(msg)
    return WatchlistConfig.model_validate(raw)
