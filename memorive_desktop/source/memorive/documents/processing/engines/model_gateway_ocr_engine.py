"""OCR of pictures and scans through the model gateway, never a model SDK directly.

While config.OCR_VIA_MODEL_GATEWAY_READY is False, document_convert_convert.py stops such files
before they reach this engine, and nothing falls back to pytesseract, marker's own OCR or a model
SDK. The flag is set when the OCR slot is ready.
"""
from __future__ import annotations

import hashlib
import mimetypes

from memorive.documents.processing.engines.base import ConvertEngine
from memorive.documents.processing.engines import register
from memorive.documents.processing.types import ConvertReport, RawObject


@register
class ModelGatewayOcrEngine(ConvertEngine):
    name = "model_gateway_ocr"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from memorive.models.gateway import ocr
        from memorive.models.gateway.errors import OcrOwnershipBlocked

        if raw.meta.data_ownership != "self":
            raise OcrOwnershipBlocked(
                "受托图片禁止远程 OCR；已在读取图片字节前挡下。"
            )
        image_bytes = raw.path.read_bytes()
        source_sha256 = hashlib.sha256(image_bytes).hexdigest()
        mime_type = mimetypes.guess_type(raw.path.name)[0] or "image/png"
        resp = ocr(
            "ocr",
            image_bytes,
            mime_type=mime_type,
            page_number=1,
            source_sha256=source_sha256,
            data_ownership=raw.meta.data_ownership,
        )
        text = resp.get("text", "")
        report = ConvertReport(
            engine=f"{self.name}+deepseek_ocr",
            pages_total=1,
            pages_failed=[],
            warnings=[],
            complete=True,
            page_details=[{
                "page_number": 1,
                "source_sha256": source_sha256,
                "ocr_text_sha256": hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                "ocr_response_sha256": resp["response_sha256"],
                "ocr_request_id": resp.get("request_id"),
                "selected_engine": "deepseek-ai/DeepSeek-OCR",
                "selection_reason": "image_requires_ocr",
            }],
        )
        return text, report
