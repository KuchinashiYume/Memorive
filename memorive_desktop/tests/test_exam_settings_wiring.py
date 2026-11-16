"""How the settings page starts a model exam, and the batch size of the local embedding exam.

No model is called.
"""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_exam_ui_has_no_native_confirmation_dialog():
    source=(ROOT/'product/web/settings_bridge.js').read_text(encoding='utf-8')
    section=source.split('const runWorkflowNodeTest =')[1].split('const addCliModel =')[0]
    assert 'window.confirm(' not in section


def test_exam_start_is_background_job_not_long_ipc_request():
    from memorive_settings.service_adapter import SETTINGS_METHODS
    assert {'settings.start_workflow_exam','settings.workflow_exam_status','settings.cancel_workflow_exam'}<=set(SETTINGS_METHODS)


def test_local_embedding_plan_matches_reused_single_input_transport():
    from memorive.exams.embedding import (
        QualityNewEmbeddingExamExecutor as Executor,
    )
    # Existing local_vector sends input:[value], not a batch of 256 values.
    assert Executor._batch_size({'kind':'LOCAL','model_name':'bge-m3:latest'})==1
    assert Executor._batch_size({'kind':'API','model_name':'BAAI/bge-m3'})==256
