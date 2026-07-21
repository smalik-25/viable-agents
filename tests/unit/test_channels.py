"""The channel, role and intent vocabulary.

These are pinned rather than merely exercised. The Phase 9 ablation matrix is
sized by the role and channel counts, and the recorded message rows from every
earlier phase are keyed on these string values, so a silent rename or addition
invalidates comparisons across runs.
"""

from viable_agents.kernel import Channel, Intent, Role


def test_five_channels_one_of_which_is_the_bypass():
    assert len(Channel) == 5
    assert Channel.ALGEDONIC in set(Channel)


def test_channel_values_are_stable_wire_strings():
    assert {c.value for c in Channel} == {
        "command",
        "coordination",
        "audit",
        "algedonic",
        "environment",
    }


def test_roles_include_the_flat_arm_so_the_ablation_is_expressible():
    # Without DISPATCHER and WORKER the Phase 9 "flat" arm would be the VSM role
    # set with permissive routing, which is a different experiment.
    assert len(Role) == 10
    assert {Role.DISPATCHER, Role.WORKER} <= set(Role)


def test_command_intents_separate_the_downward_axis_from_upward_accountability():
    # Beer's V1 (intervention) and V2 (the resource bargain and its
    # accountability return) are collapsed into one channel here. The intent is
    # what tells them apart, and it is why "an S1 must not send on COMMAND" is
    # scoped by intent rather than applied to the whole channel.
    downward = {Intent.POLICY, Intent.ALLOCATION, Intent.INTERVENTION}
    upward = {Intent.ACCOUNTABILITY}
    assert downward.isdisjoint(upward)


def test_algedonic_carries_pleasure_as_well_as_pain():
    assert Intent.PLEASURE in set(Intent)


def test_enum_members_are_str_so_they_serialize_without_a_custom_encoder():
    assert Channel.ALGEDONIC == "algedonic"
    assert Role.S3STAR == "s3star"
    assert Intent.SAMPLE_REQUEST == "sample_request"
