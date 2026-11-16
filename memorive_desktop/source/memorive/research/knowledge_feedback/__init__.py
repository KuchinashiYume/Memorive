"""Knowledge that flows back from research: knowledge drafts from answers.

research_ingest.py is used by research Q&A: an answer proposes a knowledge draft, a person reviews it,
an accepted draft becomes a note. Session refinement, which was conversation_refinement/ here, is
memorive/sessions/refinement now.

contracts.py and eligibility.py are an earlier set of knowledge feedback contracts that
knowledge_admission and artifact_registry call; canonical.py holds hashing helpers several packages
use. lineage.py and penalty.py have no caller outside the package and its tests. decision.py is the
design decision record for a derived knowledge candidate, which eligibility.py checks.
"""
