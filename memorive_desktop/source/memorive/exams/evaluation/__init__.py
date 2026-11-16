"""Model exams: question packs, protocols and scorers of each exam (card distiller, card reviewer,
analysis, OCR page, embedding, reranker).

The exam modules are not imported by plain import statements. When an exam is started on the
settings page, the exam executors in memorive/exams import them by name; memorive/exams/
resources.py gives that name (this package, or an installed model_evaluation_quality_sentinel)
and the folder of the exam packs, which stay in memorive_model_evaluation/assets and are not in the
public repository. Two helpers are imported directly: dynamic_routing_advisory.py by model_gateway
and retrieval_metrics.py by retrieval.
"""

__all__: list[str] = []
