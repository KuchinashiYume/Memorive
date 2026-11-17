"""OCR page exams: the executors of the OCR node's exam.

  v1_executor.py  the source character limits and _QualityOcrPageExamExecutorV1, the first form
  executor.py     QualityOcrPageExamExecutor, the current exam, built on the first form

QualityOcrPageExamExecutor stays importable from this package (the settings and the tests import it
from here). EXECUTOR_REF values are unchanged.
"""
from __future__ import annotations

from memorive.exams.ocr.v1_executor import (
    Core_MEAN_SOURCE_CHARS,
    EXAM_SOFT_SOURCE_CHAR_LIMIT,
    EXAM_ABSOLUTE_SOURCE_CHAR_LIMIT,
    _QualityOcrPageExamExecutorV1,
)
from memorive.exams.ocr.executor import (
    QualityOcrPageExamExecutor,
)

__all__ = [
    "Core_MEAN_SOURCE_CHARS",
    "EXAM_SOFT_SOURCE_CHAR_LIMIT",
    "EXAM_ABSOLUTE_SOURCE_CHAR_LIMIT",
    "_QualityOcrPageExamExecutorV1",
    "QualityOcrPageExamExecutor",
]
