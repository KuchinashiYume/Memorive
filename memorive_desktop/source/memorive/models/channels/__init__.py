"""Model channel attempts: one try of a model call on one channel, its receipt and its accounting.

  evidence.py               the attempt record (ChannelAttemptBuilder) and the checks on its accounting;
                            it reads the execution core's handoff events as they are
  store.py                  the append-only store of attempt records, one writer
  accounting_projection.py  accounting amendments projected without the counted total drifting
  schemas/                  the attempt record's JSON schema

Nothing in the app writes attempts yet; tests do.
"""
