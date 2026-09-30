"""图片 / 扫描件 OCR —— 经 MODEL_GATEWAY 调用,**不直连任何模型 SDK**。

⚠ ocr 槽 + 视觉/OCR adapter 未建(拍板 2 推迟);config.OCR_VIA_MODEL_GATEWAY_READY=False 时,
由 document_convert_convert 的安全阀①在进入本引擎前就挡下,绝不静默降级到
pytesseract / marker 内部 OCR / 任何直连模型 SDK。建好 ocr 槽后置 True 并补全本引擎。
"""
from __future__ import annotations

import hashlib
import mimetypes

from .base import ConvertEngine
from . import register
from ..types import ConvertReport, RawObject


@register
class ModelGatewayOcrEngine(ConvertEngine):
    name = "model_gateway_ocr"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from model_gateway import ocr
        from model_gateway.errors import OcrOwnershipBlocked

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
