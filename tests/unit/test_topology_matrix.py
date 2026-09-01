"""The routing-matrix test. CLAUDE.md hard rule 1: this file must stay green.

Four layers, strongest last:
1. totality: every (sender, recipient, channel) cell is decided by a NAMED rule.
2. reachability: no rule is dead (each wins at least one cell).
3. golden table: render() is diffed against a committed file, so a topology edit
   shows up as a reviewable diff (regenerate with --update-golden).
4. named invariants: the rules CLAUDE.md states in prose, asserted directly.

Plus the proof that topology is DATA: the flat and VSM files load into the same
class and produce different matrices, which is what makes the Phase 9 ablation a
config change.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from viable_agents.composition import load_topology
from viable_agents.kernel.channels import Channel, Intent, Role
from viable_agents.kernel.errors import TopologyViolationError
from viable_agents.kernel.topology import DEFAULT_RULE_ID

CONFIG = Path(__file__).resolve().parents[2] / "config" / "topology"
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "topology_matrix.md"

VSM = load_topology(CONFIG / "vsm.yaml")
FLAT = load_topology(CONFIG / "flat.yaml")
CELLS = list(itertools.product(Role, Role, Channel))


def _cid(cell: tuple[Role, Role, Channel]) -> str:
    s, r, c = cell
    return f"{s.value}-to-{r.value}-on-{c.value}"


# ---- layer 1: totality ----------------------------------------------------


def test_matrix_is_total() -> None:
    fell_through = [
        (s.value, r.value, c.value)
        for s, r, c in CELLS
        if VSM.decide(s, r, c).rule_id == DEFAULT_RULE_ID
    ]
    assert fell_through == [], f"{len(fell_through)} cells hit the default: {fell_through[:8]}"


def test_cell_count_is_pinned() -> None:
    assert (len(Role), len(Channel)) == (10, 5)
    assert len(CELLS) == 500


# ---- layer 2: reachability ------------------------------------------------


def test_every_rule_is_reachable() -> None:
    winners: set[str] = set()
    for s, r in itertools.product(Role, Role):
        for channel, spec in VSM.channels.items():
            winners.add(VSM.decide(s, r, channel).rule_id)
            for intent in spec.allowed_intents:
                winners.add(VSM.decide(s, r, channel, intent).rule_id)
    dead = [rule.id for rule in VSM.rules if rule.id not in winners]
    assert dead == [], f"unreachable rules (shadowed by a more specific one): {dead}"


# ---- layer 2b: bus-level enforcement, not just the matrix object ----------


@pytest.mark.parametrize("cell", CELLS, ids=_cid)
def test_decide_is_deterministic_and_check_agrees(cell: tuple[Role, Role, Channel]) -> None:
    sender, recipient, channel = cell
    decision = VSM.decide(sender, recipient, channel)
    assert VSM.decide(sender, recipient, channel) == decision
    assert VSM.is_allowed(sender, recipient, channel) is decision.allowed
    if decision.allowed:
        assert VSM.check(sender, recipient, channel).rule_id == decision.rule_id
    else:
        with pytest.raises(TopologyViolationError) as exc:
            VSM.check(sender, recipient, channel)
        assert exc.value.rule_id == decision.rule_id
        assert decision.rule_id in str(exc.value)


# ---- layer 3: golden table ------------------------------------------------


def test_matrix_matches_golden(request: pytest.FixtureRequest) -> None:
    rendered = VSM.render()
    if request.config.getoption("--update-golden"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered)
        pytest.skip("golden regenerated")
    assert GOLDEN.exists(), f"missing golden; run: uv run pytest --update-golden ({GOLDEN})"
    assert rendered == GOLDEN.read_text(), (
        "topology changed; review the diff, then: uv run pytest --update-golden"
    )


# ---- layer 4: named invariants (CLAUDE.md hard rule 1) --------------------


def test_s1_cannot_command_but_may_report_accountability() -> None:
    for recipient in Role:
        assert not VSM.is_allowed(Role.S1, recipient, Channel.COMMAND, Intent.INTERVENTION)
        assert not VSM.is_allowed(Role.S1, recipient, Channel.COMMAND, Intent.ALLOCATION)
        assert not VSM.is_allowed(Role.S1, recipient, Channel.COMMAND, Intent.POLICY)
    assert VSM.is_allowed(Role.S1, Role.S3, Channel.COMMAND, Intent.ACCOUNTABILITY)
    assert not VSM.is_allowed(Role.S1, Role.S5, Channel.COMMAND, Intent.ACCOUNTABILITY)


def test_anything_may_send_on_the_algedonic_bypass() -> None:
    for sender in Role:
        assert VSM.is_allowed(sender, Role.S5, Channel.ALGEDONIC, Intent.PAIN)


def test_algedonic_terminates_only_at_policy_or_human() -> None:
    for sender in Role:
        for recipient in Role:
            if recipient in (Role.S5, Role.HUMAN):
                continue
            assert not VSM.is_allowed(sender, recipient, Channel.ALGEDONIC, Intent.PAIN)


def test_s5_does_not_reach_operations_on_command() -> None:
    for recipient in (Role.S1, Role.S2, Role.S3STAR):
        assert not VSM.is_allowed(Role.S5, recipient, Channel.COMMAND, Intent.POLICY)
    assert VSM.is_allowed(Role.S5, Role.S3, Channel.COMMAND, Intent.POLICY)
    assert VSM.is_allowed(Role.S3, Role.S1, Channel.COMMAND, Intent.ALLOCATION)


def test_s3star_audits_operations_directly() -> None:
    assert VSM.is_allowed(Role.S3STAR, Role.S1, Channel.AUDIT, Intent.SAMPLE_REQUEST)
    assert VSM.is_allowed(Role.S3STAR, Role.S3, Channel.AUDIT, Intent.FINDING)
    assert not VSM.is_allowed(Role.S1, Role.S3, Channel.AUDIT, Intent.FINDING)


def test_declared_intents_are_respected() -> None:
    for rule in VSM.rules:
        if rule.intent != "*" and rule.channel != "*":
            assert rule.intent in VSM.channels[rule.channel].allowed_intents


# ---- topology is DATA, not code ------------------------------------------


def test_flat_and_vsm_are_the_same_class_different_files() -> None:
    # S3->S1 allocation is core VSM; the flat arm has no S3 or S1 rules at all.
    assert VSM.is_allowed(Role.S3, Role.S1, Channel.COMMAND, Intent.ALLOCATION)
    assert not FLAT.is_allowed(Role.S3, Role.S1, Channel.COMMAND, Intent.ALLOCATION)
    assert FLAT.is_allowed(Role.DISPATCHER, Role.WORKER, Channel.COMMAND, Intent.ALLOCATION)
    assert not VSM.is_allowed(Role.DISPATCHER, Role.WORKER, Channel.COMMAND, Intent.ALLOCATION)


def test_flat_disables_the_bypass() -> None:
    assert not FLAT.is_allowed(Role.WORKER, Role.S5, Channel.ALGEDONIC, Intent.PAIN)


def test_ablation_diff_is_nonempty_and_inspectable() -> None:
    diff = [
        (s.value, r.value, c.value)
        for s, r, c in CELLS
        if VSM.is_allowed(s, r, c) != FLAT.is_allowed(s, r, c)
    ]
    assert diff, "flat and VSM are identical; the ablation would measure nothing"
