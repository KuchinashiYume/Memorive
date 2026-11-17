"""Model exams: what the settings page's "test this node" runs.

  runner.py          WorkflowExams: the exam catalog, planning and running a node's exam, stored scores
  catalog.py         the exam catalog and the default exam of each workflow node
  card_distiller.py, card_reviewer/, analysis.py, embedding.py, ocr.py, reranker.py
                     the executors of each exam family (common.py and specialized_base.py are shared)
  analysis_reviewer/ the analysis reviewer exam: executor.py and its two scorer cores
  data_anomaly.py    the data anomaly exam; its pack files are in assets/data_anomaly/
  outcomes.py        how an exam ends and is scored, and when a saved score is reused
  score_history.py   the local scores; tiers.py and tiered.py shorter runs; jobs.py exam jobs
  ocr_scoring.py, retrieval_calibration.py   scoring rules of the OCR, embedding and reranker exams
  evaluation/        protocols, pack loaders and scorers, loaded by name by the executors
  resources.py       where the exam packs are (memorive_model_evaluation/assets, not in the repository)

Each executor's EXECUTOR_REF is the name saved scores are matched by; keep it unchanged.
"""
