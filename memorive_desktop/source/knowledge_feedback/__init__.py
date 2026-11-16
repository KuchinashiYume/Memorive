"""Knowledge that flows back from research: knowledge drafts from answers and session refinement items.

research_ingest.py is used by research Q&A: an answer proposes a knowledge draft, a person reviews it,
an accepted draft becomes a note. conversation_refinement/ turns imported conversations into typed
items; the sessions page reaches it through memorive_workflow_bridge, and the library uses it too.

contracts.py and eligibility.py are an earlier set of knowledge feedback contracts that
knowledge_admission and artifact_registry call; canonical.py holds hashing helpers several packages
use. lineage.py and penalty.py have no caller outside the package and its tests. This package and
decision_log import each other.
"""
