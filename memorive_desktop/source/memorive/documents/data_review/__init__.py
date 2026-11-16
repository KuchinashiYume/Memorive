"""Literature data review and a manually chosen cloud logic review.

The window reaches it through review.* methods (service.py). engine.py checks the data of a paper
without a model (processing and the inbox use it too); logic.py runs a chosen cloud model
on it; store.py keeps review state and reports. The data anomaly exam built on it is
memorive/exams/data_anomaly.py, registered with the other exams by the desktop service. health.py checks a built executable; the app does not
reach it.
"""

from memorive.documents.data_review.engine import ENGINE_VERSION, analyze

__all__ = ['ENGINE_VERSION', 'analyze']
