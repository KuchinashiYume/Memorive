"""Model exams: question packs, protocols and scorers of each exam (card distiller, card reviewer,
analysis, OCR page, embedding, reranker).

The exam modules are not imported by plain import statements. When an exam is started on the
settings page, the exam executors in memorive_settings import them by name
(service_main._quality_exam_package() names this package). Two helpers are imported directly:
dynamic_routing_advisory.py by model_gateway and retrieval_metrics.py by retrieval. Each exam's
reference pack is kept locally.
"""

__all__: list[str] = []
