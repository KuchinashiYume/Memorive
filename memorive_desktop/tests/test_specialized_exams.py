from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
while str(SOURCE_ROOT) in sys.path:
    sys.path.remove(str(SOURCE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT))

Quality_PACKAGE = "quality_specialized_test"
quality_package = ModuleType(Quality_PACKAGE)
quality_package.__path__ = [str(SOURCE_ROOT / "memorive_model_evaluation")]
quality_package.__package__ = Quality_PACKAGE
sys.modules[Quality_PACKAGE] = quality_package

from memorive.models.capabilities import (  # noqa: E402
    EMBEDDING,
    RERANKER,
    VISION_OCR,
    infer_api_model_capability,
    workflow_node_accepts_capability,
)
from memorive.models.validation.transport import HttpResponse, ProcessResult  # noqa: E402
from memorive.models.validation.runner import LiveModelValidationRunner  # noqa: E402
from memorive_settings.ocr_exam_successor import (  # noqa: E402
    MAIN_CASE_IDS,
    score_balanced_sample,
    score_case_successor,
)
from memorive_settings.specialized_exam import (  # noqa: E402
    QualityOcrPageExamExecutor,
    QualityRerankerTextExamExecutor,
)


OCR_PACK = SOURCE_ROOT / "memorive_model_evaluation" / "assets" / "ocr_page_exam" / "v3" / "reference_pack"
RERANKER_PACK = SOURCE_ROOT / "memorive_model_evaluation" / "assets" / "reranker_text_exam" / "v1" / "reference_pack"
OCR_WORKFLOW_SCORE_CONTRACT = (
    ATTEMPT_ROOT / "contracts" / "WorkflowOcrExamScoreSuccessor.json"
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_ocr_workflow_score_contract_keeps_unverified_recovery_out_of_achieved_score() -> None:
    contract = _load(OCR_WORKFLOW_SCORE_CONTRACT)

    assert contract["workflow_position"]["default_role"] == (
        "PAGE_LEVEL_RESCUE_AND_DUAL_PATH_CHECK"
    )
    assert contract["score_axes"]["unverified_recovery_ceiling"][
        "is_achieved_score"
    ] is False
    assert contract["residual_policy"]["coverage"][
        "blocks_release_until_verified"
    ] is True
    assert contract["residual_policy"]["material_safety"][
        "eligible_for_automatic_recovery_credit"
    ] is False


def _context(slot: int = 1) -> dict[str, Any]:
    return {
        "settings_revision": 9,
        "settings_sha256": "A" * 64,
        "sample_slot": slot,
    }


def _authorization(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "WorkflowModelExamAuthorization-v1",
        "authorized": True,
        "plan_sha256": plan["plan_sha256"],
        "cost_cap_cny": plan["budget_cap_cny"],
        "acknowledged_subscription_no_per_call_price": False,
    }


def _ocr_target() -> dict[str, Any]:
    return {
        "kind": "API",
        "config_id": "siliconflow-ocr",
        "provider": "SiliconFlow API Key",
        "credential_ref": "REF:test",
        "endpoint": "https://api.siliconflow.cn/v1/models",
        "model_name": "deepseek-ai/DeepSeek-OCR",
        "connection_status": "AVAILABLE",
        "image_detail": "high",
    }


def _reranker_target(model: str = "BAAI/bge-reranker-v2-m3") -> dict[str, Any]:
    return {
        "kind": "API",
        "config_id": "siliconflow-reranker",
        "provider": "SiliconFlow API Key",
        "credential_ref": "REF:test",
        "endpoint": "https://api.siliconflow.cn/v1/models",
        "model_name": model,
        "connection_status": "AVAILABLE",
    }


class _SpecializedHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, **request: Any) -> HttpResponse:
        self.calls.append(deepcopy(request))
        body = json.loads(request["body"])
        if request["url"].endswith("/chat/completions"):
            assert "max_tokens" not in body
            image_url = body["messages"][0]["content"][0]["image_url"]["url"]
            assert image_url.startswith("data:image/png;base64,")
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "model": "deepseek-ai/DeepSeek-OCR",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": "transcribed markdown"},
                            }
                        ],
                        "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                    }
                ).encode("utf-8"),
            )
        assert request["url"].endswith("/rerank")
        assert body["return_documents"] is False
        assert body["top_n"] == len(body["documents"])
        rows = [
            {"index": index, "relevance_score": float(index) / 10.0}
            for index in range(len(body["documents"]))
        ]
        return HttpResponse(
            200,
            json.dumps(
                {
                    "results": rows,
                    "meta": {"tokens": {"input_tokens": 123}},
                }
            ).encode("utf-8"),
        )


def test_specialized_capabilities_are_never_substituted_by_embedding_or_chat() -> None:
    assert infer_api_model_capability("siliconflow", "deepseek-ai/DeepSeek-OCR") == VISION_OCR
    assert infer_api_model_capability("siliconflow", "BAAI/bge-reranker-v2-m3") == RERANKER
    assert infer_api_model_capability("siliconflow", "BAAI/bge-m3") == EMBEDDING
    assert workflow_node_accepts_capability("ingest", VISION_OCR) is True
    assert workflow_node_accepts_capability("context_pack", RERANKER) is True
    assert workflow_node_accepts_capability("context_pack", EMBEDDING) is False
    assert workflow_node_accepts_capability("chunk_embedding", RERANKER) is False


def test_live_specialized_transports_use_native_api_shapes_and_provider_default_ocr_limit(tmp_path: Path) -> None:
    http = _SpecializedHttp()
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"fixture-secret",
        preferences_loader=lambda: {"proxy_mode": "NONE", "proxy_address": "", "request_timeout_seconds": 30},
        scratch_root=tmp_path,
        http_transport=http,
    )
    ocr_target = _ocr_target()
    ocr = runner.execute_ocr_image(
        profile_kind="API",
        service=ocr_target,
        model=ocr_target,
        image_bytes=b"synthetic-png",
        mime_type="image/png",
        prompt="Convert to markdown.",
        purpose="workflow_model_exam",
        max_output_tokens=None,
        timeout_seconds=30,
    )
    reranker_target = _reranker_target()
    rerank = runner.execute_rerank(
        profile_kind="API",
        service=reranker_target,
        model=reranker_target,
        query="query",
        documents=["first", "second", "third"],
        purpose="workflow_model_exam",
        timeout_seconds=30,
    )

    assert ocr["status"] == "PASS", json.dumps(ocr, ensure_ascii=False, indent=2)
    assert ocr["text"] == "transcribed markdown"
    assert ocr["raw_text"] == "transcribed markdown"
    assert ocr["normalization_applied"] is False
    assert ocr["execution_receipt"]["max_output_tokens_mode"] == "PROVIDER_DEFAULT"
    assert ocr["execution_receipt"]["request_max_output_tokens"] is None
    assert ocr["execution_receipt"]["output_limit_contract"][
        "calibrated_reply_token_ceiling"
    ] == 173_030
    assert ocr["execution_receipt"]["reply_length_quality_semantics"] == (
        "EVIDENCE_RETAINED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
    )
    assert ocr["execution_receipt"]["estimated_cost_cny"] == 0.0
    assert isinstance(ocr["execution_receipt"]["latency_ms"], int)
    assert rerank["status"] == "PASS", rerank
    assert [row["index"] for row in rerank["results"]] == [2, 1, 0]
    assert rerank["execution_receipt"]["token_usage"] == {"prompt_tokens": 123, "completion_tokens": 0}
    assert len(http.calls) == 2
    assert http.calls[0]["max_response_bytes"] is None


class _CodexOcrProcess:
    def __init__(self, *, expose_model: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.expose_model = expose_model

    def run(self, **request: Any) -> ProcessResult:
        self.calls.append(dict(request))
        argv = request["argv"]
        assert argv[argv.index("--model") + 1] == "gpt-5.6-luna"
        assert argv[argv.index("--image") + 1].endswith("input.png")
        assert Path(argv[argv.index("--image") + 1]).read_bytes() == b"synthetic-png"
        assert 'model_reasoning_effort="high"' in argv
        assert "--output-schema" not in argv
        assert request["stdin_bytes"] == b"Transcribe exactly."
        thread_started = {"type": "thread.started"}
        if self.expose_model:
            thread_started["model"] = "gpt-5.6-luna"
        events = [
            thread_started,
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "recognized text"},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 200, "output_tokens": 20},
            },
        ]
        return ProcessResult(
            True,
            0,
            b"\n".join(json.dumps(row).encode("utf-8") for row in events),
            b"",
            duration_ms=4,
        )


def test_codex_cli_ocr_transport_binds_image_model_effort_and_actual_usage(tmp_path: Path) -> None:
    process = _CodexOcrProcess()
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture-codex-binary")
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 30,
        },
        scratch_root=tmp_path,
        process_transport=process,
        executable_resolver=lambda _value: str(executable),
    )
    target = {
        "kind": "CLI",
        "config_id": "codex-luna",
        "adapter_id": "codex_cli",
        "executable": "codex",
        "model_name": "gpt-5.6-luna",
        "thinking_mode": "high",
        "cli_binary_sha256": hashlib.sha256(executable.read_bytes()).hexdigest().upper(),
        "cli_version": "codex-cli fixture",
        "cli_identity_authority_ref": "Execution_DIRECT_CLI_REQUEST_BINDING_V1_TEST",
        "connection_status": "AVAILABLE",
    }
    result = runner.execute_ocr_image(
        profile_kind="CLI",
        service=target,
        model=target,
        image_bytes=b"synthetic-png",
        mime_type="image/png",
        prompt="Transcribe exactly.",
        purpose="workflow_model_exam",
        max_output_tokens=None,
        timeout_seconds=30,
    )

    assert result["status"] == "PASS", result
    assert result["text"] == "recognized text"
    assert result["returned_model"] == "gpt-5.6-luna"
    assert result["external_process_launches"] == 1
    assert result["provider_calls"] == 0
    assert result["execution_receipt"]["token_usage"] == {
        "prompt_tokens": 200,
        "completion_tokens": 20,
    }
    assert result["execution_receipt"]["max_output_tokens_mode"] == "PROVIDER_DEFAULT"
    assert isinstance(result["execution_receipt"]["latency_ms"], int)
    assert len(process.calls) == 1


def test_codex_cli_ocr_accepts_provider_not_exposed_only_with_exact_request_binding(tmp_path: Path) -> None:
    process = _CodexOcrProcess(expose_model=False)
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture-codex-binary")
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 30,
        },
        scratch_root=tmp_path / "scratch",
        process_transport=process,
        executable_resolver=lambda _value: str(executable),
    )
    target = {
        "kind": "CLI",
        "config_id": "codex-luna",
        "adapter_id": "codex_cli",
        "executable": "codex",
        "model_name": "gpt-5.6-luna",
        "thinking_mode": "high",
        "cli_binary_sha256": hashlib.sha256(executable.read_bytes()).hexdigest().upper(),
        "cli_version": "codex-cli fixture",
        "cli_identity_authority_ref": "Execution_DIRECT_CLI_REQUEST_BINDING_V1_TEST",
        "connection_status": "AVAILABLE",
    }
    result = runner.execute_ocr_image(
        profile_kind="CLI",
        service=target,
        model=target,
        image_bytes=b"synthetic-png",
        mime_type="image/png",
        prompt="Transcribe exactly.",
        purpose="workflow_model_exam",
        max_output_tokens=None,
        timeout_seconds=30,
    )

    assert result["status"] == "PASS", result
    assert result["returned_model"] is None
    receipt = result["execution_receipt"]
    assert receipt["model_identity_grade"] == "RECORDED_UNVERIFIED"
    assert receipt["provider_event_model_identity_grade"] == "PROVIDER_NOT_EXPOSED"
    assert receipt["request_model_binding_status"] == "EXACT_ARGV_AND_FROZEN_ALLOWLIST"
    assert len(receipt["request_model_binding_sha256"]) == 64


def test_ocr_successor_local_calls_do_not_consume_external_budget(tmp_path: Path) -> None:
    executor = QualityOcrPageExamExecutor(
        scratch_root=tmp_path,
        quality_package=Quality_PACKAGE,
        reference_pack_root=OCR_PACK,
    )
    state = executor._state()
    executor._consume_result(
        state,
        {
            "external_model_calls": 1,
            "physical_model_calls": 1,
            "external_budget_calls": 0,
            "local_model_calls": 1,
            "execution_receipt": {
                "profile_kind": "LOCAL",
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "estimated_cost_cny": 0.0,
            },
        },
    )

    assert state["request_attempts"] == 1
    assert state["physical_model_calls"] == 1
    assert state["external_budget_calls"] == 0
    assert state["local_model_calls"] == 1


def test_reranker_executor_rejects_embedding_and_cli_substitution(tmp_path: Path) -> None:
    executor = QualityRerankerTextExamExecutor(
        scratch_root=tmp_path,
        quality_package=Quality_PACKAGE,
        reference_pack_root=RERANKER_PACK,
    )
    embedding = _reranker_target("BAAI/bge-m3")
    cli = {
        "kind": "CLI",
        "config_id": "cli:codex",
        "adapter_id": "codex_cli",
        "model_name": "gpt-5.6-sol",
        "thinking_mode": "high",
        "connection_status": "AVAILABLE",
    }
    assert executor.plan({"node_id": "context_pack"}, embedding, _context())["status"] == "NOT_AVAILABLE"
    assert executor.plan({"node_id": "context_pack"}, cli, _context())["status"] == "NOT_AVAILABLE"
