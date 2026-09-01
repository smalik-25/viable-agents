"""The composition root: resolve a profile into topology, fleet, and models.

A profile names one topology file, one fleet file, and one model overlay. The
Phase 9 ablation selects a profile and nothing else, so the arms differ only in
config (hard rule 7). Every run records a ``config_fingerprint`` (a hash of the
exact files that composed it) so a result is attributable to a precise
configuration and reproducible from it.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from viable_agents.kernel.channels import Role
from viable_agents.kernel.topology import Topology
from viable_agents.llm.config import ModelsConfig, load_models


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Annotated[tuple[str, ...], Field(min_length=1)]
    role: Role
    agent_type: str
    model_tier: str = "none"
    budget_usd: Decimal = Decimal("0")
    # Phase 4: which S1 units Controller sheds first under a starved run budget.
    # Higher runs longer under pressure. Config-driven since it decides
    # Phase-9-ablation-relevant behavior (hard rule 7), not a Python branch.
    priority: int = 0


class Ingress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role


class FleetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    scope: Annotated[tuple[str, ...], Field(min_length=1)]
    ingress: Ingress
    agents: list[AgentSpec] = Field(min_length=1)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    topology: str
    fleet: str
    model_overlay: str | None = None


class ResolvedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    profile: Profile
    topology: Topology
    fleet: FleetConfig
    models: ModelsConfig
    config_fingerprint: str


def _load_yaml(path: Path) -> dict[str, Any]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a mapping at top level"
        raise TypeError(msg)
    return raw


def _fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:32]


def load_topology(path: Path) -> Topology:
    """Parse a topology YAML into the kernel's pure Topology model.

    The kernel never reads YAML (hard rule 2); config compiles the file and hands
    the kernel a validated matrix. This is that compile step.
    """
    return Topology.model_validate(_load_yaml(path))


def load_profile(config_dir: Path, name: str) -> ResolvedConfig:
    profile_path = config_dir / "profiles" / f"{name}.yaml"
    profile = Profile.model_validate(_load_yaml(profile_path))

    topology_path = config_dir / "topology" / f"{profile.topology}.yaml"
    fleet_path = config_dir / "fleet" / f"{profile.fleet}.yaml"
    models_path = config_dir / "models.yaml"

    topology = load_topology(topology_path)
    fleet = FleetConfig.model_validate(_load_yaml(fleet_path))
    models = load_models(models_path)

    return ResolvedConfig(
        profile=profile,
        topology=topology,
        fleet=fleet,
        models=models,
        config_fingerprint=_fingerprint([profile_path, topology_path, fleet_path, models_path]),
    )
