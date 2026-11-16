"""Engines behind literature discovery: the multichannel slate of candidate papers and the record of
what was shown to the user (exposure_feedback.py).

The research runtime uses those two, with interactions.py (completed analyses as recommendation
evidence) and feedback_store.py (the feedback events, one writer). candidate_review_cross_candidate_card_staging.py and
knowledge_promotion_cross_atomic_card_promotion.py stage and promote candidate cards; they have no
caller outside the package and its tests.
"""
