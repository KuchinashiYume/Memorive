"""Native capability routing only; all HTTP and vectors below are synthetic."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from memorive_settings.contracts import default_settings, validate_settings
from memorive_settings.model_capabilities import infer_api_model_capability, workflow_node_accepts_capability
from memorive_settings.model_validation import HttpResponse, LiveModelValidationRunner
from memorive_settings.workflow_exam import QualityNewEmbeddingExamExecutor


ROOT = Path(__file__).resolve().parents[1]
QWEN = "Qwen/Qwen3-VL-Embedding-8B"
CASES = [
    (QWEN, "EMBEDDING"),
    (" /QWEN/QWEN3-VL-EMBEDDING-8B/ ", "EMBEDDING"),
    ("Ｑwen/Ｑwen3-VL-Embedding-8B", "EMBEDDING"),
    ("BAAI/bge-m3", "EMBEDDING"),
    ("Pro/BAAI/bge-m3", "EMBEDDING"),
    ("bge-m3:latest", "EMBEDDING"),
    ("Qwen/Qwen3-VL-Reranker-8B", "RERANKER"),
    ("BAAI/bge-reranker-v2-m3", "RERANKER"),
    ("maidalun1020/bce-reranker-base_v1", "RERANKER"),
    ("deepseek-ai/DeepSeek-OCR", "VISION_OCR"),
    ("Qwen/Qwen3-VL-8B-Instruct", "CHAT"),
    ("gpt-5.6-sol", "CHAT"),
    ("claude-fable-5", "CHAT"),
    ("custom/embedding-chat", "CHAT"),
    (QWEN + "-Instruct", "CHAT"),
    ("unknown/" + QWEN, "CHAT"),
    ("", "CHAT"),
]


@pytest.mark.parametrize("model,expected", CASES)
def test_known_capabilities_are_not_inferred_from_multimodality(model, expected):
    actual = infer_api_model_capability("siliconflow", model)
    assert actual == expected
    assert workflow_node_accepts_capability("chunk_embedding", actual) == (expected == "EMBEDDING")


def test_actual_browser_classifier_matches_backend_for_all_fixtures():
    # Evaluate the real shipped helper, not a separately maintained imitation.
    source = (ROOT / "product/web/settings_bridge.js").read_text(encoding="utf-8")
    match = re.search(r"  const inferApiModelCapability = .*?\n  };", source, re.S)
    assert match
    script = match[0] + "\nprocess.stdout.write(JSON.stringify(" + json.dumps([r[0] for r in CASES]) + ".map(m=>inferApiModelCapability('siliconflow',m))));"
    node = shutil.which("node")
    assert node, "NODE_REQUIRED_FOR_SHIPPED_FRONTEND_PARITY"
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8")
    assert json.loads(result.stdout) == [r[1] for r in CASES]


def settings_with_qwen(root):
    value = default_settings(workspace_root=str(root), artifact_root=str(root))
    ref = "REF:windows:Memorive/siliconflow/embedding-fixture"
    value["credential_references"].append(dict(credential_ref=ref, provider="SiliconFlow API Key",
        logical_key="embedding-fixture", status="STORED_UNVERIFIED", created_at="2026-09-04T00:00:00Z", updated_at="2026-09-04T00:00:00Z"))
    value["model_services"].append(dict(config_id="qwen-embedding-fixture", provider="SiliconFlow API Key",
        credential_ref=ref, plan="STANDARD", thinking=False, model_name=QWEN, tier="standard", thinking_mode="", connection_status="UNVERIFIED"))
    next(n for n in value["workflow"]["nodes"] if n["node_id"] == "chunk_embedding")["profile_ref"] = "qwen-embedding-fixture"
    return value


def test_qwen_settings_accept_embedding_and_reject_chat_node(tmp_path):
    value = settings_with_qwen(tmp_path)
    assert validate_settings(value)["model_services"][0]["model_name"] == QWEN
    next(n for n in value["workflow"]["nodes"] if n["node_id"] == "card_distill")["profile_ref"] = "qwen-embedding-fixture"
    with pytest.raises(ValueError, match="WORKFLOW_MODEL_CAPABILITY_MISMATCH:card_distill:EMBEDDING"):
        validate_settings(value)


