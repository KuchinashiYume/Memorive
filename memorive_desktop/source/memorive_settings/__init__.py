"""The settings service, and for now also the model request code and the model exams.

The window reaches it through settings.* methods (service_adapter.py maps each method to
SettingsController in controller.py). It holds three kinds of code:
  settings      store.py, contracts.py, credentials.py, cli_templates.py, the verification jobs,
                user preferences, the leaderboard and external sources;
  model calls   model_validation.py (LiveModelValidationRunner sends every model request of the app),
                model_endpoints.py, call_ledger.py, cancellable_http.py, windows_cli.py;
  model exams   workflow_exam_runner.py (WorkflowExams: the exam catalog, plans, runs and stored scores;
                it holds a LiveModelValidationRunner and sends the exam's requests through it),
                workflow_exam.py, specialized_exam.py, analysis_reviewer_exam.py, exam_successor.py,
                exam_jobs.py, exam_score_history.py and the other *exam* modules.
The model calls and the exams are to move to packages of their own; until then this is where they are.
"""

from .capability import CapabilityProjection
from .controller import SettingsController
from .credentials import CredentialReferenceManager, SyntheticDiscardCredentialBackend
from .directory_policy import DirectoryPolicy
from .model_validation import (
    FailClosedModelValidationRunner,
    LiveModelValidationRunner,
    ModelValidationRunner,
)
from .service_adapter import SETTINGS_METHODS, SettingsServiceAdapter
from .store import SettingsStore
from .workflow_exam import (
    QualityAnalysisExamExecutor,
    QualityEmbeddingExamExecutor,
    QualityNewEmbeddingExamExecutor,
    QualityNewCardDistillerLanguageBankExamExecutor,
    QualityCardDistillerDirectExamExecutor,
    QualityCardDistillerExamExecutor,
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
from .specialized_exam import (
    QualityNewRerankerTextExamExecutor,
    QualityNewRerankerTextLanguageBankExamExecutor,
    QualityNewRerankerTextProductionExamExecutor,
    QualityOcrPageExamExecutor,
    QualityRerankerTextExamExecutor,
)

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
