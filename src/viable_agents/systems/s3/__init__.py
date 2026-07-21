"""S3, Control: the here-and-now manager of the inside-and-now.

Owns the resource bargain with S1: budget in dollars and tokens flows down on
COMMAND with intent `allocation`, and keeps flowing only while accountability
flows back up. Can pause and resume workers, and emits a structured RunReport at
interval, which is the artifact S3* later audits against. Sonnet tier. Phase 4.
"""
