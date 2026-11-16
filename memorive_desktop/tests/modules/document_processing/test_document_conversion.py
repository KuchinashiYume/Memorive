from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from memorive.documents.processing.config import engine_for
from memorive.documents.processing.errors import DocumentProcessingError
from memorive.documents.processing import document_chunk_chunk
from memorive.documents.processing.document_ingest_ingest import _infer_format
from memorive.documents.processing.document_conversion import CANONICAL_FORMATS, ConversionCandidate, DocumentConversionError, FormatVerdict, RecognitionStatus, SelectionStatus
from memorive.documents.processing.document_conversion import DocumentConversionFacade, compare_candidates, probe_document
from memorive.documents.processing.document_conversion.diagnostics import parse_source_marker
from memorive.documents.processing.document_conversion.diagnostics import make_source_marker
from memorive.documents.processing.document_conversion.models import ConversionAmendment, ConversionReceipt


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00"
    b"\x18\xdd\x8d\xb1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class RegistryAndProbeTests(unittest.TestCase):
    def test_exact_fourteen_formats_and_aliases(self):
        self.assertEqual(
            set(CANONICAL_FORMATS),
            {"pdf", "md", "txt", "csv", "html", "docx", "pptx", "xlsx", "doc", "ppt", "xls", "jpeg", "png", "tiff"},
        )
        self.assertEqual(len(CANONICAL_FORMATS), 14)

    def test_unknown_extension_is_unsupported_and_never_txt(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "payload.unknown"
            source.write_text("plain text", encoding="utf-8")
            probe = probe_document(source)
            self.assertEqual(probe.status, "unsupported")
            self.assertEqual(probe.error_code, "UNSUPPORTED_FORMAT")
            self.assertIsNone(probe.detected_format)

    def test_extension_signature_mismatch_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "renamed.png"
            source.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 32 + b"\xff\xd9")
            probe = probe_document(source)
            self.assertEqual(probe.status, "identity_mismatch")
            self.assertEqual(probe.error_code, "FORMAT_IDENTITY_MISMATCH")

    def test_probe_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.txt"
            source.write_text("hello\n", encoding="utf-8")
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            result = probe_document(source)
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(result.status, "match")
            self.assertEqual(before, after)

    def test_legacy_infer_and_engine_selector_fail_closed_for_unknown(self):
        with self.assertRaises(DocumentProcessingError):
            _infer_format(Path("payload.unknown"))
        with self.assertRaises(ValueError):
            engine_for("unknown", "sandbox")


class PersistentObjectTests(unittest.TestCase):
    def test_four_axes_are_orthogonal_enums(self):
        self.assertEqual(FormatVerdict.DEGRADED.value, "degraded")
        self.assertEqual(SelectionStatus.PRIMARY_SELECTED.value, "primary_selected")
        self.assertEqual(RecognitionStatus.NOT_RUN_REQUIRES_Retrieval.value, "not_run_requires_retrieval")

    def test_receipt_identity_is_stable_and_does_not_copy_body(self):
        payload = ConversionReceipt.minimal_for_test(
            source_sha256="1" * 64,
            output_sha256="2" * 64,
            detected_format="txt",
        )
        first = payload.to_dict()
        second = payload.to_dict()
        self.assertEqual(first, second)
        self.assertNotIn("body", first)
        self.assertEqual(len(first["receipt_id"]), len("IntakeR-") + 24)

    def test_amendment_is_append_only_and_binds_predecessor(self):
        amendment = ConversionAmendment.create(
            supersedes_receipt_id="IntakeR-" + "a" * 24,
            old_output_sha256="1" * 64,
            new_output_sha256="2" * 64,
            decision="accept_corrected_output",
            reason="synthetic correction",
            evidence_refs=["fixture:Intake-TXT-POSITIVE-001"],
            decision_maker="test",
        )
        self.assertEqual(amendment.supersedes_receipt_id, "IntakeR-" + "a" * 24)
        self.assertNotEqual(amendment.old_output_sha256, amendment.new_output_sha256)

    def test_three_schemas_accept_minimal_objects(self):
        schema_root = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "processing") / "schemas"
        receipt = ConversionReceipt.minimal_for_test(
            source_sha256="1" * 64,
            output_sha256="2" * 64,
            detected_format="txt",
        ).to_dict()
        schema = json.loads((schema_root / "document_formats_conversion_receipt_v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(receipt)


class FacadeTests(unittest.TestCase):
    def test_target_outside_allowed_root_is_blocked_before_write(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            allowed = Path(temp)
            source = allowed / "sample.txt"
            source.write_text("hello\n", encoding="utf-8")
            facade = DocumentConversionFacade(allowed_root=allowed)
            with self.assertRaises(DocumentConversionError) as caught:
                facade.convert_document(
                    source,
                    paper_id="T05Guard001",
                    title="Guard",
                    target_root=Path(outside),
                    data_ownership="self",
                )
            self.assertEqual(caught.exception.code, "TARGET_OUTSIDE_ALLOWED_ROOT")
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_txt_conversion_preserves_original_hash_and_typed_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            allowed = Path(temp)
            source = allowed / "sample.txt"
            source.write_text("alpha\nbeta\n", encoding="utf-8")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest().upper()
            result = DocumentConversionFacade(allowed_root=allowed).convert_document(
                source,
                paper_id="T05Text001",
                title="Text",
                target_root=allowed / "outputs",
                data_ownership="self",
            )
            self.assertEqual(result.receipt.source_sha256, source_hash)
            self.assertEqual(result.receipt.format_verdict, "supported")
            self.assertTrue(result.original_path.is_file())
            self.assertEqual(hashlib.sha256(result.original_path.read_bytes()).hexdigest().upper(), source_hash)
            self.assertIn("memorive:source:v1", result.rawmd_path.read_text(encoding="utf-8"))
            self.assertIsNone(result.pdf_path)

    def test_png_preparation_never_claims_recognition(self):
        with tempfile.TemporaryDirectory() as temp:
            allowed = Path(temp)
            source = allowed / "sample.png"
            source.write_bytes(PNG_1X1)
            result = DocumentConversionFacade(allowed_root=allowed).convert_document(
                source,
                paper_id="T05Image001",
                title="Image",
                target_root=allowed / "outputs",
                data_ownership="self",
            )
            self.assertEqual(result.receipt.recognition_status, "not_run_requires_retrieval")
            self.assertEqual(result.receipt.format_verdict, "degraded")
            self.assertIsNotNone(result.receipt.image_frame_manifest)

    def test_conversion_never_overwrites_existing_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            allowed = Path(temp)
            source = allowed / "sample.txt"
            source.write_text("alpha\n", encoding="utf-8")
            facade = DocumentConversionFacade(allowed_root=allowed)
            kwargs = dict(
                paper_id="T05NoOverwrite001", title="No overwrite",
                target_root=allowed / "outputs", data_ownership="self",
            )
            facade.convert_document(source, **kwargs)
            with self.assertRaises(DocumentConversionError) as caught:
                facade.convert_document(source, **kwargs)
            self.assertEqual(caught.exception.code, "TARGET_ALREADY_EXISTS")


class SelectionAndAnchorTests(unittest.TestCase):
    def test_primary_usable_is_never_overwritten_by_failed_candidate(self):
        primary = ConversionCandidate("primary", "completed", "supported", [], "a" * 64)
        failed = ConversionCandidate("candidate", "failed", "unsupported", ["ENGINE_ERROR"], None)
        decision = compare_candidates([primary, failed])
        self.assertEqual(decision.selected_candidate_id, "primary")
        self.assertEqual(decision.selection_status, "primary_selected")

    def test_one_qualified_fallback_has_explicit_trace(self):
        primary = ConversionCandidate("primary", "failed", "unsupported", ["EMPTY_OUTPUT"], None)
        fallback = ConversionCandidate("fallback", "completed", "degraded", [], "b" * 64)
        decision = compare_candidates([primary, fallback])
        self.assertEqual(decision.selected_candidate_id, "fallback")
        self.assertEqual(decision.selection_status, "fallback_selected")
        self.assertEqual(decision.primary_candidate_id, "primary")

    def test_source_marker_round_trip(self):
        marker = make_source_marker("slide", 3, label="Synthetic")
        parsed = parse_source_marker(marker)
        self.assertEqual(parsed, {"kind": "slide", "ordinal": 3, "label": "Synthetic"})

    def test_typed_source_anchor_is_bound_into_chunk_schema_v3(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cleanmd = root / "clean.md"
            cleanmd.write_text(
                "---\n"
                "paper_id: T05Anchor001\n"
                "title: Anchor\n"
                "data_ownership: self\n"
                f"source_sha256: {'a' * 64}\n"
                "converter_profile_id: document_formats_local_v1\n"
                "convert_receipt_id: IntakeR-aaaaaaaaaaaaaaaaaaaaaaaa\n"
                "---\n"
                + make_source_marker("slide", 2, label="Synthetic")
                + "\nslide body\n",
                encoding="utf-8",
            )
            (root / "[T05Anchor001] Anchor").mkdir()
            with mock.patch.object(document_chunk_chunk, "library_root", return_value=root):
                output = document_chunk_chunk.chunk(cleanmd)
            chunks = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]["chunk_schema_version"], document_chunk_chunk.CHUNK_SCHEMA_VERSION)
            self.assertEqual(
                chunks[0]["source_anchor"],
                {
                    "kind": "slide",
                    "ordinal": 2,
                    "label": "Synthetic",
                    "source_sha256": "a" * 64,
                    "converter_profile_id": "document_formats_local_v1",
                    "conversion_receipt_id": "IntakeR-aaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            self.assertNotIn("memorive:source", chunks[0]["text"])


if __name__ == "__main__":
    unittest.main()
