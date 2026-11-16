"""Knowledge that flows back from research: knowledge drafts from answers.

research_ingest.py is used by research Q&A: an answer proposes a knowledge draft, a person reviews it,
an accepted draft becomes a note. Session refinement, which was conversation_refinement/ here, is
memorive/sessions/refinement now.

contracts.py and eligibility.py are an earlier set of knowledge feedback contracts that
the card states and the document registry call; canonical.py offers the hashing helpers of
platform/jcs.py and platform/hashed_payloads.py under its older name. lineage.py and penalty.py have no caller outside the package and its tests. decision.py is the
design decision record for a derived knowledge candidate, which eligibility.py checks.
"""
