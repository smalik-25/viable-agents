"""Load a scenario YAML into a typed, validated config.

Kept out of ``sources/events.py`` and ``generator.py`` for the same reason
``composition.py`` is separate from the kernel: YAML parsing is I/O and belongs in
one place, not scattered across every consumer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FlakyTestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    test_id: str
    flake_rate: float = Field(ge=0.0, le=1.0)


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    seed: int
    event_count: int = Field(gt=0)
    repos: list[str] = Field(min_length=1)
    workflow_names: list[str] = Field(min_length=1)
    branches: list[str] = Field(min_length=1)
    base_failure_rate: float = Field(ge=0.0, le=1.0)
    anomaly_rates: dict[str, float]
    flaky_tests: list[FlakyTestSpec] = Field(min_length=1)
    dependency_manifests: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _rates_do_not_exceed_one(self) -> ScenarioConfig:
        total = sum(self.anomaly_rates.values())
        if total > 1.0 + 1e-9:
            msg = f"anomaly_rates sum to {total}, which exceeds 1.0"
            raise ValueError(msg)
        return self


def load_scenario(path: Path) -> ScenarioConfig:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a mapping at top level"
        raise TypeError(msg)
    return ScenarioConfig.model_validate(raw)
