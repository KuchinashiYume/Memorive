"""Jobs: their contracts, the job stores, state transitions and the checks before a job starts.

  contracts.py             job control states, canonical JSON and hashes, field checks, the resolved
                           profile snapshot
  durable_store.py         the desktop service's jobs, events and idempotency keys in SQLite
  sandbox_store.py         the sandbox's job store in plain files
  lifecycle.py             legal state transitions, and recovery classification of a stopped job
  capabilities.py          the capability snapshot and the preflight before a job starts
  workflow_definitions.py  workflow definitions read from one folder
"""
