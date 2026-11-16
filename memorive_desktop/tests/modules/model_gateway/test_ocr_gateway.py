"""DeepSeek-OCR gateway, SiliconFlow adapter, and RUNTIME_LOG accounting contracts."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch

from memorive.platform.logs.records import TASK_TYPES, log_accounting
from memorive.models.gateway.config import GatewayConfig, SlotConfig
from memorive.models.gateway.errors import (
    DependencyError,
    OcrOwnershipBlocked,
    OcrResultInvalid,
)
from memorive.models.gateway.fallback import FallbackPolicy, ROOT_SERVICE_UNAVAILABLE
from memorive.models.gateway.gateway import ModelGateway
from memorive.models.gateway.prompts import PromptRegistry
from memorive.models.gateway.providers.siliconflow import SiliconFlowAdapter


SOURCE_SHA256 = "a" * 64
GATEWAY_ROOT = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "models" / "gateway")


class _Config:
    def __init__(self) -> None:
        self.calls = 0
        self.slot_value = SimpleNamespace(
            task_type="ocr",
            provider="siliconflow",
            model_id="deepseek-ai/DeepSeek-OCR",
            api_key_env="SILICONFLOW_API_KEY",
            prompt_version=3,
        )

    def slot(self, task_type: str):
        self.calls += 1
        if task_type != "ocr":
            raise AssertionError(task_type)
        return self.slot_value


class _Prompts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def get_or_none(self, task_type: str, version: int | None = None):
        if task_type != "ocr":
            raise AssertionError(task_type)
        self.calls.append((task_type, version))
        return SimpleNamespace(
            version=3,
            body="<|grounding|>Convert the document to markdown.",
        )


class _Router:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[tuple[str, dict, str]] = []

    def dispatch(
        self,
        task_type: str,
        payload: dict,
        *,
        capability: str = "chat",
    ) -> dict:
        self.calls.append((task_type, payload, capability))
        return dict(self.result)


class _Fallback:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, task_type: str, slot, operation):
        self.calls += 1
        return operation(), 1


class _Cost:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record(
        self,
        task_type: str,
        result: dict,
        slot,
        prompt_version,
        *,
        cache_hit: bool,
        retries: int = 0,
    ) -> dict:
        self.calls.append(
            (task_type, dict(result), slot, prompt_version, cache_hit, retries)
        )
        return result


def _gateway(result: dict) -> tuple[ModelGateway, _Config, _Router, _Fallback, _Cost]:
    gateway = object.__new__(ModelGateway)
    config = _Config()
    router = _Router(result)
    fallback = _Fallback()
    cost = _Cost()
    gateway.config = config
    gateway.prompts = _Prompts()
    gateway.router = router
    gateway.fallback = fallback
    gateway.cost = cost
    return gateway, config, router, fallback, cost


def _valid_result(**overrides) -> dict:
    result = {
        "task_type": "ocr",
        "provider": "siliconflow",
        "model": "deepseek-ai/DeepSeek-OCR",
        "text": "# OCR text\n\nvalue 123",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
        "page_number": 2,
        "source_sha256": SOURCE_SHA256,
        "response_sha256": "b" * 64,
        "request_id": "chatcmpl-test",
    }
    result.update(overrides)
    return result


class OcrGatewayTest(unittest.TestCase):
    def test_live_ocr_slot_pins_markdown_prompt_away_from_qualification_successors(self) -> None:
        config = GatewayConfig(GATEWAY_ROOT / "models.yaml")
        prompts = PromptRegistry(GATEWAY_ROOT / "prompts")

        slot = config.slot("ocr")
        self.assertEqual(slot.prompt_version, 3)
        self.assertGreaterEqual(prompts.latest_version("ocr") or 0, 5)
        production_prompt = prompts.get("ocr", slot.prompt_version)
        self.assertIn("Convert the document to markdown", production_prompt.body)
        self.assertNotIn("PageRecognitionNormalizedPageTranscription", production_prompt.body)

    def test_entrusted_data_is_blocked_before_slot_or_router(self) -> None:
        gateway, config, router, fallback, cost = _gateway(_valid_result())

        with self.assertRaises(OcrOwnershipBlocked):
            gateway.ocr(
                "ocr",
                b"png-bytes",
                mime_type="image/png",
                page_number=2,
                source_sha256=SOURCE_SHA256,
                data_ownership="entrusted",
            )

        self.assertEqual(config.calls, 0)
        self.assertEqual(router.calls, [])
        self.assertEqual(fallback.calls, 0)
        self.assertEqual(cost.calls, [])

    def test_valid_page_uses_ocr_capability_and_records_after_validation(self) -> None:
        gateway, _, router, _, cost = _gateway(_valid_result())

        result = gateway.ocr(
            "ocr",
            b"png-bytes",
            mime_type="image/png",
            page_number=2,
            source_sha256=SOURCE_SHA256,
            data_ownership="self",
        )

        self.assertEqual(result["text"], "# OCR text\n\nvalue 123")
        self.assertEqual(len(router.calls), 1)
        task_type, payload, capability = router.calls[0]
        self.assertEqual((task_type, capability), ("ocr", "ocr"))
        self.assertEqual(payload["image_bytes"], b"png-bytes")
        self.assertEqual(payload["page_number"], 2)
        self.assertEqual(payload["source_sha256"], SOURCE_SHA256)
        self.assertEqual(
            payload["prompt"],
            "<|grounding|>Convert the document to markdown.",
        )
        self.assertEqual(gateway.prompts.calls, [("ocr", 3)])
        self.assertEqual(len(cost.calls), 1)
        self.assertEqual(cost.calls[0][0], "ocr")
        self.assertEqual(cost.calls[0][3], "v3")
        self.assertFalse(cost.calls[0][4])
        self.assertEqual(cost.calls[0][5], 1)

    def test_invalid_outputs_are_rejected_before_accounting(self) -> None:
        cases = (
            ("empty", {"text": "  "}),
            ("page_mismatch", {"page_number": 3}),
            ("source_mismatch", {"source_sha256": "c" * 64}),
            ("missing_response_hash", {"response_sha256": None}),
        )
        for label, override in cases:
            with self.subTest(label=label):
                gateway, _, _, _, cost = _gateway(_valid_result(**override))
                with self.assertRaises(OcrResultInvalid):
                    gateway.ocr(
                        "ocr",
                        b"png-bytes",
                        mime_type="image/png",
                        page_number=2,
                        source_sha256=SOURCE_SHA256,
                        data_ownership="self",
                    )
                self.assertEqual(cost.calls, [])


class SiliconFlowOcrAdapterTest(unittest.TestCase):
    def test_request_shape_and_normalized_response(self) -> None:
        response_obj = {
            "id": "chatcmpl-ocr-1",
            "model": "deepseek-ai/DeepSeek-OCR",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            "<|ref|>title<|/ref|>"
                            "<|det|>[[118, 74, 707, 98]]<|/det|>\n"
                            "# Parsed\n\n"
                            "<|ref|>text<|/ref|>"
                            "<|det|>[[118, 134, 496, 150]]<|/det|>\n"
                            "42 kg"
                        )
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }
        response_bytes = json.dumps(response_obj).encode("utf-8")
        captured: dict = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self) -> bytes:
                return response_bytes

        def _urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response()

        slot = SlotConfig(
            task_type="ocr",
            enabled=True,
            provider="siliconflow",
            model_id="deepseek-ai/DeepSeek-OCR",
            endpoint="https://api.siliconflow.cn/v1/chat/completions",
            api_key_env="SILICONFLOW_API_KEY",
            temperature=0.0,
            timeout_seconds=45,
            image_detail="high",
            max_tokens=4096,
        )
        payload = {
            "image_bytes": b"\x89PNG\r\n",
            "mime_type": "image/png",
            "page_number": 7,
            "source_sha256": SOURCE_SHA256,
            "prompt": "<|grounding|>Convert the document to markdown.",
        }

        with (
            patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-secret"}),
            patch("memorive.models.gateway.providers.siliconflow.urllib.request.urlopen", _urlopen),
        ):
            result = SiliconFlowAdapter().ocr(slot, payload)

        request_obj = captured["request"]
        body = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(captured["timeout"], 45)
        self.assertEqual(body["model"], "deepseek-ai/DeepSeek-OCR")
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["max_tokens"], 4096)
        self.assertFalse(body["stream"])
        parts = body["messages"][0]["content"]
        self.assertEqual(parts[0]["type"], "image_url")
        image_url = parts[0]["image_url"]
        self.assertEqual(image_url["detail"], "high")
        self.assertEqual(
            image_url["url"],
            "data:image/png;base64," + base64.b64encode(payload["image_bytes"]).decode("ascii"),
        )
        self.assertEqual(parts[1], {"type": "text", "text": payload["prompt"]})
        self.assertEqual(result["text"], "# Parsed\n\n42 kg")
        self.assertNotIn("<|ref|>", result["text"])
        self.assertNotIn("[[118", result["text"])
        self.assertEqual(result["page_number"], 7)
        self.assertEqual(result["source_sha256"], SOURCE_SHA256)
        self.assertEqual(
            result["response_sha256"], hashlib.sha256(response_bytes).hexdigest()
        )
        self.assertEqual(result["request_id"], "chatcmpl-ocr-1")
        self.assertNotIn("test-secret", json.dumps(body))

    def test_http_400_surfaces_sanitized_provider_detail(self) -> None:
        response_body = json.dumps({
            "code": 20015,
            "message": "max_tokens is too large",
        }).encode("utf-8")

        def _urlopen(request, timeout):
            del timeout
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(response_body),
            )

        slot = SlotConfig(
            task_type="ocr",
            enabled=True,
            provider="siliconflow",
            model_id="deepseek-ai/DeepSeek-OCR",
            endpoint="https://api.siliconflow.cn/v1/chat/completions",
            api_key_env="SILICONFLOW_API_KEY",
            timeout_seconds=45,
            image_detail="high",
            max_tokens=4096,
        )
        payload = {
            "image_bytes": b"\x89PNG\r\n",
            "mime_type": "image/png",
            "page_number": 1,
            "source_sha256": SOURCE_SHA256,
            "prompt": "convert",
        }
        with (
            patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-secret"}),
            patch(
                "memorive.models.gateway.providers.siliconflow.urllib.request.urlopen",
                _urlopen,
            ),
            self.assertRaises(HTTPError) as caught,
        ):
            SiliconFlowAdapter().ocr(slot, payload)

        message = str(caught.exception)
        self.assertIn("provider_code=20015", message)
        self.assertIn("max_tokens is too large", message)
        self.assertNotIn("test-secret", message)
        self.assertNotIn("data:image", message)


class OcrHttpFallbackTest(unittest.TestCase):
    def test_http_400_is_blocked_without_retry(self) -> None:
        calls = 0
        error = HTTPError(
            "https://api.siliconflow.cn/v1/chat/completions",
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b"{}"),
        )
        slot = SimpleNamespace(
            provider="siliconflow",
            model_id="deepseek-ai/DeepSeek-OCR",
        )

        def dispatch():
            nonlocal calls
            calls += 1
            raise error

        with (
            patch("memorive.models.gateway.fallback.log_error") as mocked_log,
            self.assertRaises(DependencyError) as caught,
        ):
            FallbackPolicy(backoff=(0, 0)).run("ocr", slot, dispatch)

        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.root_class, ROOT_SERVICE_UNAVAILABLE)
        self.assertEqual(mocked_log.call_count, 1)
        self.assertEqual(mocked_log.call_args.kwargs["context"]["action"], "block")


class M11OcrAccountingContractTest(unittest.TestCase):
    def test_ocr_is_a_hard_enum_and_can_be_recorded(self) -> None:
        self.assertIn("ocr", TASK_TYPES)
        with patch("memorive.platform.logs.records.log") as mocked_log:
            log_accounting(
                "ocr",
                "deepseek-ai/DeepSeek-OCR",
                prompt_version="v1",
                tokens={"prompt": 1, "completion": 2, "reasoning": 0, "total": 3},
                quota_account="SILICONFLOW_API_KEY",
            )
        self.assertEqual(mocked_log.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
