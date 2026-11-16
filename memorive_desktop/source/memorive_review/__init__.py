"""Literature data review and a manually chosen cloud logic review.

The window reaches it through review.* methods (service.py). engine.py checks the data of a paper
without a model; logic.py runs a chosen cloud model on it; store.py keeps review state and reports.
exam.py is the data anomaly exam, started from the settings page like the other exams. health.py
checks a built executable and is run by hand.
"""

from .engine import ENGINE_VERSION, analyze

__all__ = ['ENGINE_VERSION', 'analyze']
