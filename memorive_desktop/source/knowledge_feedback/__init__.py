"""Knowledge that flows back from research: knowledge drafts from answers and session refinement items.

research_ingest.py is used by research Q&A: an answer proposes a knowledge draft, a person reviews it,
an accepted draft becomes a note. conversation_refinement/ turns imported conversations into typed
items that the sessions page shows for review.

contracts.py, eligibility.py, lineage.py and penalty.py are an earlier set of knowledge feedback
contracts (checks and dry-run adapters) that other packages call; the window does not.
"""
