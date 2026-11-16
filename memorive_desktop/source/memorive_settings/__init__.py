"""The settings service, and for now also the model exams.

The window reaches it through settings.* methods (service_adapter.py maps each method to
SettingsController in controller.py). It holds two kinds of code:
  settings      store.py, contracts.py, credentials.py, cli_template_management.py, the verification
                jobs, user preferences, the leaderboard and external sources;
  model exams   workflow_exam_runner.py (WorkflowExams: the exam catalog, plans, runs and stored scores;
                it holds a LiveModelValidationRunner and sends the exam's requests through it),
                workflow_exam/ (one module per exam family), specialized_exam/ (OCR, reranker),
                analysis_reviewer_exam.py, exam_successor.py, exam_jobs.py, exam_score_history.py and
                the other *exam* modules.
The model requests themselves are in memorive/models: the controller holds the runner from
memorive/models/validation/runner.py. The exams move to memorive/exams.
"""

from .capability import CapabilityProjection
from .controller import SettingsController
from .credentials import CredentialReferenceManager, SyntheticDiscardCredentialBackend
from .directory_policy import DirectoryPolicy
from memorive.models.validation.runner import (
    FailClosedModelValidationRunner,
    LiveModelValidationRunner,
    ModelValidationRunner,
)
from .service_adapter import SETTINGS_METHODS, SettingsServiceAdapter
from .store import SettingsStore
from memorive_settings.workflow_exam.analysis import QualityAnalysisExamExecutor
from memorive_settings.workflow_exam.embedding import (
    QualityEmbeddingExamExecutor,
    QualityNewEmbeddingExamExecutor,
)
from memorive_settings.workflow_exam.card_distiller import (
    QualityNewCardDistillerLanguageBankExamExecutor,
    QualityCardDistillerDirectExamExecutor,
    QualityCardDistillerExamExecutor,
)
from memorive_settings.workflow_exam.card_reviewer import (
    QualityCardReviewerExamExecutor,
    QualityCardReviewerExamSuccessorExecutor,
)
from .quality_new import (
    DEFAULT_WORKFLOW_MAPPING,
    Quality_NEW_CATALOG_SCHEMA,
    build_quality_new_catalog,
    evaluate_defect_lifecycle,
    plan_exam_volume,
    project_cached_entry,
    validate_quality_new_catalog,
)
from .exam_successor import (
    CategoryBinding,
    ExecutionOutcome,
    Scorability,
    TerminalDisposition,
)
from .analysis_reviewer_exam import QualityAnalysisReviewerExamSuccessorExecutor
from memorive_settings.specialized_exam.reranker import (
    QualityNewRerankerTextExamExecutor,
    QualityNewRerankerTextLanguageBankExamExecutor,
    QualityNewRerankerTextProductionExamExecutor,
    QualityRerankerTextExamExecutor,
)
from memorive_settings.specialized_exam.ocr import QualityOcrPageExamExecutor

__all__ = [
    "CapabilityProjection",
    "CategoryBinding",
    "CredentialReferenceManager",
    "DirectoryPolicy",
    "DEFAULT_WORKFLOW_MAPPING",
    "ExecutionOutcome",
    "FailClosedModelValidationRunner",
    "LiveModelValidationRunner",
    "ModelValidationRunner",
    "QualityAnalysisExamExecutor",
    "QualityAnalysisReviewerExamSuccessorExecutor",
    "QualityEmbeddingExamExecutor",
    "QualityNewEmbeddingExamExecutor",
    "QualityNewCardDistillerLanguageBankExamExecutor",
    "QualityNewRerankerTextExamExecutor",
    "QualityNewRerankerTextLanguageBankExamExecutor",
    "QualityNewRerankerTextProductionExamExecutor",
    "QualityOcrPageExamExecutor",
    "QualityRerankerTextExamExecutor",
    "QualityCardDistillerDirectExamExecutor",
    "QualityCardDistillerExamExecutor",
    "QualityCardReviewerExamExecutor",
    "QualityCardReviewerExamSuccessorExecutor",
    "Quality_NEW_CATALOG_SCHEMA",
    "Scorability",
    "SETTINGS_METHODS",
    "SettingsController",
    "SettingsServiceAdapter",
    "SettingsStore",
    "SyntheticDiscardCredentialBackend",
    "TerminalDisposition",
    "build_quality_new_catalog",
    "evaluate_defect_lifecycle",
    "plan_exam_volume",
    "project_cached_entry",
    "validate_quality_new_catalog",
]
