"""The workflow exams: what runs a node's exam and keeps its scores.

  metadata.py            the metadata fields saved scores carry, by revision, and which workflow
                         node each exam role binds to
  catalog_validation.py  reading a stored exam catalog of any revision; WorkflowExams loads its
                         catalog through it
  workflow_exams.py      WorkflowExams: the exam catalog, planning and running a node's exam, the
                         stored scores; FailClosedWorkflowExams when exams are unavailable

WorkflowExams and FailClosedWorkflowExams stay importable from this package (the desktop service, the
settings controller and the tests import them from here).
"""
from __future__ import annotations

from memorive.exams.runner.metadata import (
    WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4,
    WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5,
    WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6,
    WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7,
    WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8,
    WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS,
    WORKFLOW_EXAM_SCORE_METADATA_FIELDS,
    WORKFLOW_EXAM_ROLE_NODE_BINDINGS,
)
from memorive.exams.runner.workflow_exams import (
    WorkflowExams,
    FailClosedWorkflowExams,
)

__all__ = [
    "WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4",
    "WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5",
    "WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6",
    "WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7",
    "WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8",
    "WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS",
    "WORKFLOW_EXAM_SCORE_METADATA_FIELDS",
    "WORKFLOW_EXAM_ROLE_NODE_BINDINGS",
    "WorkflowExams",
    "FailClosedWorkflowExams",
]
