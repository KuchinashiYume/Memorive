"""The settings service.

The window reaches it through settings.* methods (service_adapter.py maps each method to
SettingsController in controller.py): store.py, contracts.py, credentials.py,
cli_template_management.py, the verification jobs, user preferences, the leaderboard and external
sources. The model requests are in memorive/models (the controller holds the runner from
memorive/models/validation/runner.py), the model exams in memorive/exams (the controller holds
WorkflowExams from memorive/exams/runner.py). This package still exports the exam executors by name
for its callers.
"""

from memorive.desktop.settings.capability import CapabilityProjection
from memorive.desktop.settings.controller import SettingsController
from memorive.desktop.settings.credentials import CredentialReferenceManager, SyntheticDiscardCredentialBackend
from memorive.desktop.settings.directory_policy import DirectoryPolicy
from memorive.models.validation.runner import (
    FailClosedModelValidationRunner,
    LiveModelValidationRunner,
    ModelValidationRunner,
)
from memorive.desktop.settings.service_adapter import SETTINGS_METHODS, SettingsServiceAdapter
from memorive.desktop.settings.store import SettingsStore
from memorive.exams.analysis import QualityAnalysisExamExecutor
from memorive.exams.embedding import (
    QualityEmbeddingExamExecutor,
    QualityNewEmbeddingExamExecutor,
)
from memorive.exams.card_distiller import (
    QualityNewCardDistillerLanguageBankExamExecutor,
    QualityCardDistillerDirectExamExecutor,
    QualityCardDistillerExamExecutor,
)
from memorive.exams.card_reviewer import (
    QualityCardReviewerExamExecutor,
    QualityCardReviewerExamSuccessorExecutor,
)
from memorive.exams.catalog import (
    DEFAULT_WORKFLOW_MAPPING,
    Quality_NEW_CATALOG_SCHEMA,
    build_quality_new_catalog,
    evaluate_defect_lifecycle,
    plan_exam_volume,
    project_cached_entry,
    validate_quality_new_catalog,
)
from memorive.exams.outcomes import (
    CategoryBinding,
    ExecutionOutcome,
    Scorability,
    TerminalDisposition,
)
from memorive.exams.analysis_reviewer.executor import QualityAnalysisReviewerExamSuccessorExecutor
from memorive.exams.reranker import (
    QualityNewRerankerTextExamExecutor,
    QualityNewRerankerTextLanguageBankExamExecutor,
    QualityNewRerankerTextProductionExamExecutor,
    QualityRerankerTextExamExecutor,
)
from memorive.exams.ocr import QualityOcrPageExamExecutor

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
