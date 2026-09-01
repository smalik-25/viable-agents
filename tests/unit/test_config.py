"""The config layer is what makes the Phase 9 ablation config-only.

A profile resolves to a topology, a fleet roster with an ingress binding, and a
model set, plus a deterministic fingerprint so a run is attributable to the exact
files that composed it. The flat and full-VSM profiles differ in which agents
exist and where events enter, which a routing matrix alone cannot express.
"""

from __future__ import annotations

from pathlib import Path

from viable_agents.composition import load_profile
from viable_agents.kernel.channels import Role

CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_full_vsm_profile_resolves() -> None:
    resolved = load_profile(CONFIG, "full-vsm")
    assert resolved.topology.name == "vsm"
    assert resolved.fleet.ingress.role is Role.S1
    roles = {a.role for a in resolved.fleet.agents}
    assert {Role.S1, Role.S3, Role.S5} <= roles


def test_flat_profile_has_a_different_population_and_ingress() -> None:
    resolved = load_profile(CONFIG, "flat")
    assert resolved.topology.name == "flat"
    assert resolved.fleet.ingress.role is Role.DISPATCHER
    roles = {a.role for a in resolved.fleet.agents}
    assert Role.DISPATCHER in roles
    assert Role.S1 not in roles


def test_fingerprint_is_deterministic() -> None:
    a = load_profile(CONFIG, "full-vsm")
    b = load_profile(CONFIG, "full-vsm")
    assert a.config_fingerprint == b.config_fingerprint
    flat = load_profile(CONFIG, "flat")
    assert flat.config_fingerprint != a.config_fingerprint


def test_s2_has_no_model_tier() -> None:
    resolved = load_profile(CONFIG, "full-vsm")
    assert resolved.models.tier_for_role("s2") is None


def test_cheap_overlay_demotes_the_metasystem() -> None:
    resolved = load_profile(CONFIG, "flat")
    # The cheap overlay maps sonnet roles to haiku for iteration runs.
    assert resolved.models.tier_for_role("s3", overlay="cheap") == "haiku"
    assert resolved.models.tier_for_role("s3") == "sonnet"
