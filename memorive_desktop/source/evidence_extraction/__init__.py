"""Card extraction: a model reads a paper's CleanMD chunks and fills the fields of an evidence card.

distill.py runs it for one paper; segmented_distill/ does the same for long papers in segments;
normalize.py, anchor_repair.py and core_language_recovery.py fix what the model returned before the
card is written; card.py writes the card. The workflow (memorive_workflow) calls these for each paper.

The fact_identity_* modules derive fact identities and required fields of a card; other
packages call them, the window does not.
"""
from .distill import DistillResult, distill

__all__ = ["distill", "DistillResult"]
