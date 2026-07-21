"""S4, Intelligence: the outside-and-then.

Reads the environment stream and run history, forecasts CI event volume, failure
rate, spend and API quota consumption, and proposes plans to S5. Where S4's
proposal conflicts with S3's current allocation, both positions go to S5 with
rationales; that tension is the model's central homeostat, and S5's arbitration
of it is logged. Sonnet tier. Phase 8.
"""
