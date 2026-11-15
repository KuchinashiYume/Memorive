"""Page-scoped OCR rescue, dual-route comparison, and pipeline stop tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model_gateway.errors import OcrOwnershipBlocked

from document_processing import document_convert_convert, pipeline
from document_processing.engines.model_gateway_ocr_engine import ModelGatewayOcrEngine
from document_processing.errors import DocumentProcessingError
from document_processing.ocr_page_rescue import rescue_pdf_pages
from document_processing.types import (
    ConvertReport,
    IngestMeta,
    RawObject,
)


def _page(revision: int, text: str) -> str:
    return f"<!-- memorive:page:{revision} -->\n\n{text}\n\n"


def _raw(path: Path, ownership: str = "self") -> RawObject:
    return RawObject(
        path=path,
        meta=IngestMeta(
            source=str(path),
            imported_at="2026-07-14T00:00:00+08:00",
            data_ownership=ownership,
            doc_type="literature",
            original_format="pdf",
        ),
    )


def _image_raw(path: Path, ownership: str) -> RawObject:
    return RawObject(
        path=path,
        meta=IngestMeta(
            source=str(path),
            imported_at="2026-07-14T00:00:00+08:00",
            data_ownership=ownership,
            doc_type="literature",
            original_format="image",
        ),
    )


def _primary_text() -> str:
    return (
        _page(1, "Readable scientific introduction 12 kg. " * 8)
        + _page(2, "�" * 100)
        + _page(3, "Readable conclusion DOI 10.1000/test. " * 8)
    )


class PageRescueTest(unittest.TestCase):
    def test_only_bad_page_is_rendered_and_replaced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocr_rescue_") as td:
            source = Path(td) / "source.pdf"
            source.write_bytes(b"isolated-pdf-source")
            rendered: list[int] = []
            called: list[int] = []

            def render_page(path: Path, page_number: int) -> bytes:
                self.assertEqual(path, source)
                rendered.append(page_number)
                return f"png-{page_number}".encode()

            def ocr_client(task_type: str, image_bytes: bytes, **kwargs) -> dict:
                self.assertEqual(task_type, "ocr")
                self.assertEqual(image_bytes, b"png-2")
                called.append(kwargs["page_number"])
                return {
                    "text": "Recovered table value 456 mg and DOI 10.1000/rescue.",
                    "page_number": kwargs["page_number"],
                    "source_sha256": kwargs["source_sha256"],
                    "response_sha256": "b" * 64,
                    "request_id": "ocr-2",
                }

            rebuilt, report = rescue_pdf_pages(
                _raw(source),
                _primary_text(),
                ConvertReport(engine="pymupdf4llm", pages_total=3),
                render_page=render_page,
                ocr_client=ocr_client,
            )

        self.assertEqual(rendered, [2])
        self.assertEqual(called, [2])
        self.assertIn("Recovered table value 456 mg", rebuilt)
        self.assertNotIn("�" * 20, rebuilt)
        self.assertTrue(report.complete)
        self.assertEqual(report.pages_failed, [])
        self.assertEqual(len(report.page_details), 1)
        detail = report.page_details[0]
        self.assertEqual(detail["page_number"], 2)
        self.assertEqual(detail["selected_engine"], "deepseek-ai/DeepSeek-OCR")
        self.assertEqual(detail["selection_reason"], "primary_unusable_ocr_valid")
        self.assertRegex(detail["primary_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(detail["ocr_text_sha256"], r"^[0-9a-f]{64}$")

    def test_forced_dual_check_keeps_usable_primary_and_records_conflict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocr_dual_") as td:
            source = Path(td) / "source.pdf"
            source.write_bytes(b"source")
            primary = _page(1, "Digital source reports 12 kg and 98% accuracy.")

            def ocr_client(task_type: str, image_bytes: bytes, **kwargs) -> dict:
                del task_type, image_bytes
                return {
                    "text": "OCR reports 17 kg and 89% accuracy.",
                    "page_number": 1,
                    "source_sha256": kwargs["source_sha256"],
                    "response_sha256": "c" * 64,
                    "request_id": "ocr-check-1",
                }

            rebuilt, report = rescue_pdf_pages(
                _raw(source),
                primary,
                ConvertReport(engine="pymupdf4llm", pages_total=1),
                forced_pages=[1],
                render_page=lambda path, page: b"png",
                ocr_client=ocr_client,
            )

        self.assertIn("Digital source reports 12 kg and 98%", rebuilt)
        self.assertNotIn("OCR reports 17 kg", rebuilt)
        detail = report.page_details[0]
        self.assertEqual(detail["comparison"], "conflict")
        self.assertEqual(detail["selected_engine"], "pymupdf4llm")
        self.assertEqual(detail["selection_reason"], "usable_primary_retained")
        self.assertEqual(detail["primary_only_tokens"], ["12", "98%"])
        self.assertEqual(detail["ocr_only_tokens"], ["17", "89%"])

    def test_failed_bad_page_preserves_other_pages_and_marks_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocr_partial_") as td:
            source = Path(td) / "source.pdf"
            source.write_bytes(b"source")

            def fail_ocr(*args, **kwargs):
                raise RuntimeError("simulated OCR outage")

            rebuilt, report = rescue_pdf_pages(
                _raw(source),
                _primary_text(),
                ConvertReport(engine="pymupdf4llm", pages_total=3),
                render_page=lambda path, page: b"png",
                ocr_client=fail_ocr,
            )

        self.assertIn("Readable scientific introduction", rebuilt)
        self.assertIn("Readable conclusion DOI", rebuilt)
        self.assertFalse(report.complete)
        self.assertEqual(report.pages_failed, [2])
        self.assertEqual(report.page_details[0]["selected_engine"], "pymupdf4llm")
        self.assertEqual(report.page_details[0]["selection_reason"], "ocr_failed")
        self.assertNotIn("simulated OCR outage", report.warnings)

    def test_entrusted_pdf_is_blocked_before_source_read_or_render(self) -> None:
        missing_source = Path("must-not-be-opened.pdf")
        render_calls: list[int] = []

        with self.assertRaises(OcrOwnershipBlocked):
            rescue_pdf_pages(
                _raw(missing_source, ownership="entrusted"),
                _page(1, "�" * 100),
                ConvertReport(engine="pymupdf4llm", pages_total=1),
                render_page=lambda path, page: render_calls.append(page) or b"png",
                ocr_client=lambda *args, **kwargs: {},
            )

        self.assertEqual(render_calls, [])


class ImageOcrOwnershipTest(unittest.TestCase):
    def test_entrusted_image_is_blocked_before_file_read(self) -> None:
        missing_source = Path("entrusted-image-must-not-be-read.png")
        with (
            mock.patch("model_gateway.ocr") as mocked_ocr,
            self.assertRaises(OcrOwnershipBlocked),
        ):
            ModelGatewayOcrEngine().convert(
                _image_raw(missing_source, ownership="entrusted")
            )
        mocked_ocr.assert_not_called()


class _FakeCorruptEngine:
    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        del raw
        return _page(1, "�" * 100), ConvertReport(
            engine="fake_corrupt",
            pages_total=1,
        )


class _RecordingRegistry:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.commit_calls = 0

    def ensure(self, paper_id: str, folder: Path) -> None:
        del paper_id, folder
        self.ensure_calls += 1

    def commit(self, paper_id: str, folder: Path, *, confirmed: bool = True) -> None:
        del paper_id, folder, confirmed
        self.commit_calls += 1


class PartialRawMdIntegrationTest(unittest.TestCase):
    def test_ocr_failure_writes_auditable_partial_rawmd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocr_partial_rawmd_") as td:
            root = Path(td)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-isolated")
            library = root / "library"
            registry = _RecordingRegistry()
            partial_report = ConvertReport(
                engine="pymupdf4llm+deepseek_ocr",
                pages_total=1,
                pages_failed=[1],
                warnings=["ocr_page_failed:1:RuntimeError"],
                complete=False,
                page_details=[
                    {
                        "page_number": 1,
                        "selected_engine": "pymupdf4llm",
                        "selection_reason": "ocr_failed",
                    }
                ],
            )

            with (
                mock.patch.object(document_convert_convert, "OCR_VIA_MODEL_GATEWAY_READY", True),
                mock.patch.object(
                    document_convert_convert, "get_engine", return_value=_FakeCorruptEngine()
                ),
                mock.patch.object(
                    document_convert_convert,
                    "rescue_pdf_pages",
                    return_value=(_page(1, "�" * 100), partial_report),
                ),
                mock.patch.object(
                    document_convert_convert, "PaperIdRegistry", return_value=registry
                ),
                mock.patch.object(document_convert_convert, "library_root", return_value=library),
                mock.patch.object(document_convert_convert, "log"),
                mock.patch.object(document_convert_convert, "log_error"),
            ):
                result = document_convert_convert.convert(
                    _raw(source),
                    paper_id="Partial2026",
                    title="Partial OCR",
                    engine="fake_corrupt",
                    allow_short=True,
                )

            text = result.rawmd_path.read_text(encoding="utf-8")
            self.assertFalse(result.report.complete)
            self.assertIn('"complete": false', text)
            self.assertIn('"pages_failed": [1]', text)
            self.assertEqual(registry.ensure_calls, 1)
            self.assertEqual(registry.commit_calls, 1)


class PartialFrontmatterGuardTest(unittest.TestCase):
    def test_partial_rawmd_cannot_resume_into_m1c(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocr_resume_guard_") as td:
            rawmd = Path(td) / "partial.md"
            rawmd.write_text(
                "---\n"
                "paper_id: Partial2026\n"
                "title: Partial\n"
                "data_ownership: self\n"
                "doc_type: literature\n"
                "convert_report:\n"
                "  complete: false\n"
                "  pages_failed: [2]\n"
                "---\n\n"
                "<!-- memorive:page:1 -->\nkept page\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(pipeline, "log_error") as mocked_log,
                self.assertRaisesRegex(DocumentProcessingError, r"pages_failed=\[2\]"),
            ):
                pipeline._read_frontmatter(rawmd)
        self.assertEqual(mocked_log.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
