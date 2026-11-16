"""Cost routing advice: which cheaper model a call could have used, and what became of that advice.

  advisory_ledger.py      append-only ledger of rejected and withheld advice
  historical_recovery.py  historical model-call records classified as cost routing evidence
  decision_preview.py     the design decision record a piece of advice would become, previewed

The advice itself is made in gateway/routing_advisory.py. Nothing in the app calls these three yet.
"""
