# Routing matrix: vsm (v1)

Default effect: `deny`. 23 rules over 5 channels.

| id | sender | recipient | channel | intent | effect | reason |
|----|--------|-----------|---------|--------|--------|--------|
| default_deny | * | * | * | * | deny | not on the VSM map |
| cmd_s5_s3_policy | s5 | s3 | command | policy | allow | S5 sets policy to control |
| cmd_s5_s4_policy | s5 | s4 | command | policy | allow | S5 sets policy to intelligence |
| cmd_s3_s1_alloc | s3 | s1 | command | allocation | allow | resource bargain, downward |
| cmd_s3_s1_intervene | s3 | s1 | command | intervention | allow | Beer V1: intervention spends S1 autonomy |
| cmd_s3_s1_pause | s3 | s1 | command | pause | allow | S3 pauses an over-budget worker |
| cmd_s3_s1_resume | s3 | s1 | command | resume | allow | S3 resumes a worker |
| cmd_s1_s3_account | s1 | s3 | command | accountability | allow | closes Beer V2: accountability flows up as resources flow down |
| coord_s2_s1_arbitrate | s2 | s1 | coordination | arbitrate | allow | S2 arbitrates a claim conflict |
| coord_s2_s1_lateral | s2 | s1 | coordination | lateral | allow | S2 lateral service to operations |
| coord_s1_s2_claim | s1 | s2 | coordination | claim | allow | S1 claims work through the S2 ledger |
| coord_s1_s2_release | s1 | s2 | coordination | release | allow | S1 releases a claim |
| coord_s3_s4_homeostat | s3 | s4 | coordination | homeostat | allow | S3/S4 homeostat |
| coord_s4_s3_homeostat | s4 | s3 | coordination | homeostat | allow | S3/S4 homeostat |
| audit_s3star_s1_sample | s3star | s1 | audit | sample_request | allow | V4: sporadic audit reads operations directly |
| audit_s3star_s3_finding | s3star | s3 | audit | finding | allow | audit findings return to control |
| audit_s3star_s5_charter | s3star | s5 | audit | charter_diff | allow | charter divergence surfaces to policy |
| alg_any_s5 | * | s5 | algedonic | * | allow | the bypass: anything reaches policy |
| alg_any_human | * | human | algedonic | * | allow | pain also surfaces to the human |
| env_in_s1 | environment | s1 | environment | observation | allow | CI events enter at operations |
| env_s1_out | s1 | environment | environment | observation | allow | operations act on the environment (read-only in this fleet) |
| env_in_s4 | environment | s4 | environment | observation | allow | S4 scans the environment |
| env_s4_forecast | s4 | environment | environment | forecast | allow | S4 emits forecasts about the environment |
