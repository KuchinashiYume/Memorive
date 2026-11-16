"""Model exams: question packs, protocols and scorers of each exam (card distiller, card reviewer,
analysis, OCR page, embedding, reranker).

Nothing imports these modules by a plain import statement. When an exam is started on the settings
page, the exam executors in memorive_settings import them by name
(service_main._quality_exam_package() names this package). Each exam's reference pack is kept locally.
"""

__all__: list[str] = []
