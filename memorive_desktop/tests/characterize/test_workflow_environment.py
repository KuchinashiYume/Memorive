"""How the workflow executor points the processing modules at its own data folders.

Pins today's behaviour of CorePipelineExecutor._bind_environment before the planned
change (it refuses to run when one of its environment variables is already set to a
different value). Paths are shown relative to the runtime folder; os.environ and the
memorive.documents.processing.config roots are restored after each case.
"""
import os
from pathlib import Path
from types import SimpleNamespace

from memorive.documents.processing import config as processing_config
from memorive.documents.workflow.executor import CorePipelineExecutor
from tests.characterize.golden import check

HERE = Path(__file__).parent
NAMES = ("MEMORIVE_VAULT_ROOT", "MEMORIVE_DOCUMENT_PROCESSING_VAULT_ROOT", "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT",
         "MEMORIVE_OPS_ROOT", "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT", "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_CHROMA_DIR",
         "MEMORIVE_MODEL_GATEWAY_CACHE_DIR", "MEMORIVE_RUNTIME_LOG_LOG_DIR", "MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR",
         "MEMORIVE_EVIDENCE_REVIEW_REPAIR_DIR", "MEMORIVE_EVIDENCE_REVIEW_JUDGMENT_DEBT_DIR",
         "MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR", "MEMORIVE_RETRIEVAL_RERANK_ENABLED",
         "MEMORIVE_DOCUMENT_PROCESSING_OCR_VIA_MODEL_GATEWAY_READY")


def _executor(root):
    root = Path(root).resolve()
    return SimpleNamespace(runtime_root=root, sandbox_root=root / "sandbox", ops_root=root / "ops",
                           chroma_root=root / "vector-store", chroma_dir=root / "vector-store" / "chroma-sandbox-v2")


def _relative(value, root):
    text = str(value)
    return text.replace(str(root), "<runtime>").replace("\\", "/")


def _bind(monkeypatch, root, preset):
    # monkeypatch.delenv does not record a variable that is not set, so it would not remove
    # what _bind_environment adds; later service children would inherit those values and
    # refuse to start. Restore the whole environment by hand instead.
    saved = dict(os.environ)
    for attribute in ("VAULT_ROOT", "OPS_ROOT", "SANDBOX_ROOT", "CHROMA_ROOT"):
        monkeypatch.setattr(processing_config, attribute, getattr(processing_config, attribute))
    try:
        for name in NAMES:
            os.environ.pop(name, None)
        os.environ.update(preset)
        executor = _executor(root)
        try:
            CorePipelineExecutor._bind_environment(executor)
        except RuntimeError as error:
            return {"error": _relative(error, executor.runtime_root)}
        return {
            "environment": {name: _relative(os.environ.get(name), executor.runtime_root) for name in NAMES},
            "processing_config": {a: _relative(getattr(processing_config, a), executor.runtime_root)
                                  for a in ("VAULT_ROOT", "OPS_ROOT", "SANDBOX_ROOT", "CHROMA_ROOT")},
        }
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_environment_is_restored_afterwards(tmp_path, monkeypatch):
    before = dict(os.environ)
    _bind(monkeypatch, (tmp_path / "runtime").resolve(), {})
    assert dict(os.environ) == before


def test_bind_environment(tmp_path, monkeypatch):
    root = (tmp_path / "runtime").resolve()
    cases = {}
    with monkeypatch.context() as patch:
        cases["nothing_preset"] = _bind(patch, root, {})
    with monkeypatch.context() as patch:
        cases["same_values_preset"] = _bind(patch, root, {"MEMORIVE_OPS_ROOT": str(root / "ops"),
                                                          "MEMORIVE_RETRIEVAL_RERANK_ENABLED": "false"})
    with monkeypatch.context() as patch:
        cases["other_ops_folder_preset"] = _bind(patch, root, {"MEMORIVE_OPS_ROOT": str(root / "elsewhere")})
    with monkeypatch.context() as patch:
        cases["rerank_enabled_preset"] = _bind(patch, root, {"MEMORIVE_RETRIEVAL_RERANK_ENABLED": "true"})
    with monkeypatch.context() as patch:
        cases["unrelated_variable_preset"] = _bind(patch, root, {"MEMORIVE_WORKSPACE_ROOT": str(root / "w")})
    check("workflow_environment", cases, HERE)
