"""Embedding exams: the executors of the embedding node's exam.

  executor.py         QualityEmbeddingExamExecutor, the reference exam the others build on
  new_executor.py     QualityNewEmbeddingExamExecutor, the current exam
  legacy_executor.py  QualityNewEmbeddingLegacyExamExecutor, the earlier form kept for its saved scores

Every executor stays importable from this package (the settings and the tests import from here).
EXECUTOR_REF values are unchanged.
"""
from __future__ import annotations

from memorive.exams.embedding.executor import (
    QualityEmbeddingExamExecutor,
)
from memorive.exams.embedding.legacy_executor import (
    QualityNewEmbeddingLegacyExamExecutor,
)
from memorive.exams.embedding.new_executor import (
    QualityNewEmbeddingExamExecutor,
)

__all__ = [
    "QualityEmbeddingExamExecutor",
    "QualityNewEmbeddingLegacyExamExecutor",
    "QualityNewEmbeddingExamExecutor",
]
