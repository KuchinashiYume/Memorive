"""Regression tests for deterministic PDF text-integrity fail-closed gates."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memorive.documents.processing import document_convert_convert
from memorive.documents.processing.errors import TextIntegrityError
from memorive.documents.processing.document_convert_convert import _validate_pdf_text_integrity
from memorive.documents.processing.types import ConvertReport, IngestMeta, RawObject


def _page(revision: int, text: str) -> str:
    return f"<!-- memorive:page:{revision} -->\n\n{text}\n\n"


class PdfTextIntegrityTest(unittest.TestCase):
    def test_clean_text_passes(self) -> None:
        raw_md = "".join(
            _page(i, "Readable scientific text 123. " * 20)
            for i in range(1, 11)
        )

        _validate_pdf_text_integrity(raw_md, pages_total=10)

    def test_tab_newline_and_carriage_return_pass(self) -> None:
        raw_md = _page(1, "column_a\tcolumn_b\r\nvalue_a\tvalue_b")

        _validate_pdf_text_integrity(raw_md, pages_total=1)

    def test_one_locally_damaged_page_out_of_ten_stays_below_gate(self) -> None:
        raw_md = _page(1, "�" * 30) + "".join(
            _page(i, "Readable scientific text 123. " * 20)
            for i in range(2, 11)
        )

        _validate_pdf_text_integrity(raw_md, pages_total=10)

    def test_document_wide_replacement_corruption_fails(self) -> None:
        raw_md = "".join(
            _page(i, ("�" * 100) + "A")
            for i in range(1, 9)
        )

        with self.assertRaisesRegex(
            TextIntegrityError,
            r"全文替换字符比例.*20\.0000%",
        ):
            _validate_pdf_text_integrity(raw_md, pages_total=8)

    def test_twenty_percent_corrupt_pages_fails(self) -> None:
        damaged = ("�" * 30) + ("A" * 70)
        raw_md = _page(1, damaged) + _page(2, damaged) + "".join(
            _page(i, "Readable scientific text 123. " * 20)
            for i in range(3, 11)
        )

        with self.assertRaisesRegex(
            TextIntegrityError,
            r"高替换字符页 2/10.*20\.0000%",
        ):
            _validate_pdf_text_integrity(raw_md, pages_total=10)

    def test_one_locally_control_damaged_page_out_of_ten_stays_below_gate(
        self,
    ) -> None:
        raw_md = _page(1, "\x01" * 30) + "".join(
            _page(i, "Readable scientific text 123. " * 20)
            for i in range(2, 11)
        )

        _validate_pdf_text_integrity(raw_md, pages_total=10)

    def test_document_wide_control_corruption_fails(self) -> None:
        raw_md = "".join(
            _page(i, ("\x01" * 100) + "A")
            for i in range(1, 9)
        )

        with self.assertRaisesRegex(
            TextIntegrityError,
            r"全文控制字符比例.*20\.0000%",
        ):
            _validate_pdf_text_integrity(raw_md, pages_total=8)

    def test_twenty_percent_control_corrupt_pages_fails(self) -> None:
        damaged = ("\x01" * 30) + ("A" * 70)
        raw_md = _page(1, damaged) + _page(2, damaged) + "".join(
            _page(i, "Readable scientific text 123. " * 20)
            for i in range(3, 11)
        )

        with self.assertRaisesRegex(
            TextIntegrityError,
            r"高控制字符页 2/10.*20\.0000%",
        ):
            _validate_pdf_text_integrity(raw_md, pages_total=10)


class _FakeCorruptEngine:
    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        del raw
        raw_md = "".join(_page(i, "�" * 100) for i in range(1, 6))
        return raw_md, ConvertReport(
            engine="fake_corrupt",
            pages_total=5,
            pages_failed=[],
            warnings=[],
        )


class _RecordingRegistry:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.commit_calls = 0

    def ensure(self, paper_id: str, folder: Path) -> None:
        del paper_id, folder
        self.ensure_calls += 1

    def commit(
        self,
        paper_id: str,
        folder: Path,
        *,
        confirmed: bool = True,
    ) -> None:
        del paper_id, folder, confirmed
        self.commit_calls += 1


class ConvertFailClosedTest(unittest.TestCase):
    def test_corrupt_text_fails_before_files_or_registry_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            source = temp_root / "source.pdf"
            source.write_bytes(b"%PDF-test")
            library = temp_root / "library"
            registry = _RecordingRegistry()
            raw = RawObject(
                path=source,
                meta=IngestMeta(
                    source=str(source),
                    imported_at="2026-07-12T00:00:00+08:00",
                    data_ownership="self",
                    doc_type="literature",
                    original_format="pdf",
                ),
            )

            with (
                mock.patch.object(
                    document_convert_convert,
                    "get_engine",
                    return_value=_FakeCorruptEngine(),
                ),
                mock.patch.object(
                    document_convert_convert,
                    "PaperIdRegistry",
                    return_value=registry,
                ),
                mock.patch.object(
                    document_convert_convert,
                    "library_root",
                    return_value=library,
                ),
                mock.patch.object(document_convert_convert, "log"),
                mock.patch.object(document_convert_convert, "log_error"),
            ):
                with self.assertRaises(TextIntegrityError):
                    document_convert_convert.convert(
                        raw,
                        paper_id="Corrupt2026",
                        title="Corrupt input",
                        engine="fake_corrupt",
                        allow_short=True,
                    )

            self.assertEqual(registry.ensure_calls, 1)
            self.assertEqual(registry.commit_calls, 0)
            self.assertFalse(library.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
