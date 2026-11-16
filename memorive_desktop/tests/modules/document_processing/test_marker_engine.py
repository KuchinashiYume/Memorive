from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from memorive.documents.processing.engines.marker_engine import MarkerEngine
from memorive.documents.processing.errors import EngineNotAvailable
from memorive.documents.processing.types import IngestMeta, RawObject


def _raw(path: Path) -> RawObject:
    return RawObject(
        path=path,
        meta=IngestMeta(
            source=str(path),
            imported_at="2026-08-21T00:00:00+09:00",
            data_ownership="self",
            doc_type="literature",
            original_format="pdf",
        ),
    )


class _Rendered:
    def __init__(self, markdown: str, *, pages: int = 2):
        self.markdown = markdown
        self.metadata = {"page_stats": [{} for _ in range(pages)]}


class MarkerEngineTests(unittest.TestCase):
    def _modules(self, rendered: _Rendered):
        calls: dict[str, object] = {}

        class PdfConverter:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def __call__(self, path: str):
                calls["path"] = path
                return rendered

        marker = types.ModuleType("marker")
        converters = types.ModuleType("marker.converters")
        pdf = types.ModuleType("marker.converters.pdf")
        models = types.ModuleType("marker.models")
        output = types.ModuleType("marker.output")
        pdf.PdfConverter = PdfConverter
        models.create_model_dict = lambda: {"model": "fake-offline"}
        output.text_from_rendered = lambda value: (value.markdown, ".md", {})
        return calls, {
            "marker": marker,
            "marker.converters": converters,
            "marker.converters.pdf": pdf,
            "marker.models": models,
            "marker.output": output,
        }

    def test_installed_marker_converts_and_reports_bound_output(self):
        markdown = (
            "<!-- memorive:page:1 -->\n\nfirst\n\n"
            "<!-- memorive:page:2 -->\n\nsecond\n"
        )
        calls, modules = self._modules(_Rendered(markdown))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules):
            path = Path(tmp) / "input.pdf"
            path.write_bytes(b"%PDF-fake")
            text, report = MarkerEngine().convert(_raw(path))

        self.assertEqual(text, markdown)
        self.assertEqual(report.engine, "marker")
        self.assertEqual(report.pages_total, 2)
        self.assertTrue(report.complete)
        self.assertEqual(report.pages_failed, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(calls["path"], str(path))
        self.assertEqual(calls["init"]["artifact_dict"], {"model": "fake-offline"})
        self.assertTrue(calls["init"]["config"]["paginate_output"])
        self.assertEqual(len(report.source_sha256), 64)
        self.assertEqual(len(report.output_sha256), 64)

    def test_metadata_page_count_is_truthful_when_marker_has_no_page_boundaries(self):
        calls, modules = self._modules(_Rendered("full document\n", pages=3))
        del calls
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules):
            path = Path(tmp) / "input.pdf"
            path.write_bytes(b"%PDF-fake")
            text, report = MarkerEngine().convert(_raw(path))

        self.assertEqual(text, "full document\n")
        self.assertEqual(report.pages_total, 3)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("页面级 OCR rescue", report.warnings[0])

    def test_empty_marker_output_fails_closed(self):
        _, modules = self._modules(_Rendered(""))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules):
            path = Path(tmp) / "input.pdf"
            path.write_bytes(b"%PDF-fake")
            with self.assertRaisesRegex(RuntimeError, "空或非文本 Markdown"):
                MarkerEngine().convert(_raw(path))

    def test_missing_marker_dependency_does_not_fallback(self):
        real_import = __import__

        def blocked_import(name, *args, **kwargs):
            if name.startswith("marker"):
                raise ImportError("marker intentionally absent")
            return real_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, patch("builtins.__import__", blocked_import):
            path = Path(tmp) / "input.pdf"
            path.write_bytes(b"%PDF-fake")
            with self.assertRaises(EngineNotAvailable):
                MarkerEngine().convert(_raw(path))

    def test_non_sequential_page_markers_fail_closed(self):
        _, modules = self._modules(
            _Rendered("<!-- memorive:page:1 -->\nfirst\n<!-- memorive:page:3 -->\nthird\n")
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules):
            path = Path(tmp) / "input.pdf"
            path.write_bytes(b"%PDF-fake")
            with self.assertRaisesRegex(RuntimeError, "页标记不连续"):
                MarkerEngine().convert(_raw(path))


if __name__ == "__main__":
    unittest.main()
