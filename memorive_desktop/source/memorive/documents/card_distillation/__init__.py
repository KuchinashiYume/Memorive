"""Card extraction: a model reads a paper's CleanMD chunks and fills the fields of an evidence card.

distill.py runs it for one paper; segmented_distill/ does the same for long papers in segments;
normalize.py, anchor_repair.py and core_language_recovery.py fix what the model returned before the
card is written; card.py writes the card. The workflow (memorive_workflow) calls distill, card,
config, response_contracts and segmented_distill; evidence_review and research_recommendations
call distill and card.

The three fact_identity_* modules derive fact identities and required fields of a card. The app
does not reach them; evidence_review imports the contract core.
"""
from memorive.documents.card_distillation.distill import DistillResult, distill

__all__ = ["distill", "DistillResult"]
