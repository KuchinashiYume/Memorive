"""Regressions for one field-local repair of invented core chunk ids."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

anchor_repair = import_module("memorive.documents.card_distillation.anchor_repair")
distill = import_module("memorive.documents.card_distillation.distill")

PAPER_ID = "NewPerspectivesAD"


def _chunks() -> dict[str, str]:
    chunks = {
        f"{PAPER_ID}#c{revision:04d}": (
            f"Source chunk {revision}. EGSB treatment condition {revision}; "
            "cold wastewater, dilute wastewater, formaldehyde, and reactor evidence."
        )
        for revision in range(1, 40)
    }
    chunks[f"{PAPER_ID}#c0039"] = "UNRELATED_SECRET_CHUNK_WITH_NO_TARGET_TERMS"
    return chunks


def _core(*, bad_key_results: bool = True, bad_boundary: bool = True, only_bad: bool = False) -> dict:
    fields = {
        "research_question": "Question",
        "research_object": ["Object"],
        "method": ["Method"],
        "key_results": "Quantitative results from several operating conditions.",
        "author_conclusion": "Conclusion",
        "boundary_conditions": ["Cold", "Dilute", "Formaldehyde"],
    }
    obj = {}
    for offset, (field, value) in enumerate(fields.items(), start=1):
        obj[field] = {
            "value": value,
            "anchors": [{"chunk_id": f"{PAPER_ID}#c{offset:04d}", "quote": None}],
        }
    if bad_key_results:
        obj["key_results"]["anchors"] = [] if only_bad else [
            {"chunk_id": f"{PAPER_ID}#c0025", "quote": None},
        ]
        obj["key_results"]["anchors"].append(
            {"chunk_id": f"{PAPER_ID}#c0033_0037", "quote": None}
        )
    if bad_boundary:
        obj["boundary_conditions"]["anchors"] = [
            {"chunk_id": f"{PAPER_ID}#c0031", "quote": None},
            {"chunk_id": f"{PAPER_ID}#c0032", "quote": None},
            {"chunk_id": f"{PAPER_ID}#c0033_0035", "quote": None},
        ]
    return obj


def _bundle_from_prompt(prompt: str) -> dict:
    marker = "BUNDLE\n"
    return _wire_bundle(prompt, prompt.split(marker, 1)[1])


def _valid_response(prompt: str) -> dict:
    bundle = _bundle_from_prompt(prompt)
    repairs = []
    for target in bundle["targets"]:
        replacements = [
            {"chunk_id": chunk_id, "quote": None}
            for chunk_id in target["allowed_chunk_ids"]
            if chunk_id in {
                f"{PAPER_ID}#c0033",
                f"{PAPER_ID}#c0034",
                f"{PAPER_ID}#c0035",
                f"{PAPER_ID}#c0036",
                f"{PAPER_ID}#c0037",
            }
        ]
        repairs.append({
            "field": target["field"],
            "anchor_index": target["anchor_index"],
            "bad_chunk_id": target["bad_chunk_id"],
            "action": "replace",
            "replacement_anchors": replacements,
            "reason": "Expanded the compressed range into supplied real chunks.",
        })
    return {
        "text": json.dumps({
            "paper_id": bundle["paper_id"],
            "authoritative_sha256": bundle["authoritative_sha256"],
            "repairs": repairs,
        })
    }


class CoreAnchorRepairTest(unittest.TestCase):
    def test_card_create_repair_and_regenerate_budgets_are_independent(self) -> None:
        from memorive.documents.card_distillation import config
        from memorive.documents.card_review.atomic_repair import ATOMIC_REPAIR_MAX_TOKENS

        self.assertEqual(config.CARD_CREATE_MAX_TOKENS, 8192)
        self.assertEqual(config.CARD_CREATE_MAX_TOKENS_RETRY, 16384)
        self.assertEqual(config.CARD_REGENERATE_MAX_TOKENS, 16384)
        self.assertEqual(ATOMIC_REPAIR_MAX_TOKENS, 32768)

    def test_real_composite_ids_are_repaired_in_one_consolidated_call(self) -> None:
        original = _core()
        calls = []

        def call(prompt: str):
            calls.append(prompt)
            return _valid_response(prompt)

        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=call,
        )

        self.assertEqual(result.status, "repaired")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result.issues), 2)
        self.assertFalse(anchor_repair.collect_core_anchor_issues(result.obj, _chunks()))
        for field in original:
            self.assertEqual(result.obj[field]["value"], original[field]["value"])
        self.assertNotIn("UNRELATED_SECRET_CHUNK_WITH_NO_TARGET_TERMS", calls[0])

    def test_contract_failure_removes_only_invalid_anchor_entries(self) -> None:
        original = _core()

        def bad_hash(prompt: str):
            bundle = _bundle_from_prompt(prompt)
            return {"text": json.dumps({
                "paper_id": bundle["paper_id"],
                "authoritative_sha256": "0" * 64,
                "repairs": [],
            })}

        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=bad_hash,
        )

        self.assertEqual(result.status, "fallback_dropped_invalid_anchors")
        self.assertEqual(len(result.dropped_anchor_entries), 2)
        self.assertEqual(result.obj["key_results"]["value"], original["key_results"]["value"])
        self.assertEqual(
            [row["chunk_id"] for row in result.obj["key_results"]["anchors"]],
            [f"{PAPER_ID}#c0025"],
        )
        self.assertEqual(
            [row["chunk_id"] for row in result.obj["boundary_conditions"]["anchors"]],
            [f"{PAPER_ID}#c0031", f"{PAPER_ID}#c0032"],
        )

    def test_cross_paper_or_non_whitelisted_replacement_is_not_applied(self) -> None:
        original = _core(bad_boundary=False)

        def cross_paper(prompt: str):
            bundle = _bundle_from_prompt(prompt)
            target = bundle["targets"][0]
            return {"text": json.dumps({
                "paper_id": bundle["paper_id"],
                "authoritative_sha256": bundle["authoritative_sha256"],
                "repairs": [{
                    "field": target["field"],
                    "anchor_index": target["anchor_index"],
                    "bad_chunk_id": target["bad_chunk_id"],
                    "action": "replace",
                    "replacement_anchors": [{"chunk_id": "OtherPaper#c0033", "quote": None}],
                    "reason": "invalid test response",
                }],
            })}

        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=cross_paper,
        )
        self.assertEqual(result.status, "fallback_dropped_invalid_anchors")
        self.assertNotIn("OtherPaper#c0033", json.dumps(result.obj))

    def test_malformed_anchor_index_is_a_contract_fallback_not_a_crash(self) -> None:
        original = _core(bad_boundary=False)

        def malformed(prompt: str):
            bundle = _bundle_from_prompt(prompt)
            target = bundle["targets"][0]
            return {"text": json.dumps({
                "paper_id": bundle["paper_id"],
                "authoritative_sha256": bundle["authoritative_sha256"],
                "repairs": [{
                    "field": target["field"],
                    "anchor_index": [],
                    "bad_chunk_id": target["bad_chunk_id"],
                    "action": "drop_anchor",
                    "replacement_anchors": [],
                    "reason": "malformed test response",
                }],
            })}

        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=malformed,
        )
        self.assertEqual(result.status, "fallback_dropped_invalid_anchors")
        self.assertIn("anchor_index", result.error)

    def test_safe_fallback_refuses_to_leave_a_field_without_any_anchor(self) -> None:
        original = _core(bad_boundary=False, only_bad=True)
        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=lambda _prompt: {"text": "not json"},
        )
        self.assertEqual(result.status, "unrecoverable")
        self.assertIn("key_results", result.error)

    def test_no_issue_makes_no_model_call_and_no_change(self) -> None:
        original = _core(bad_key_results=False, bad_boundary=False)
        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=lambda _prompt: self.fail("repair call must not run"),
        )
        self.assertEqual(result.status, "not_needed")
        self.assertEqual(result.attempts, 0)
        self.assertEqual(result.obj, original)

    def test_trace_writer_never_overwrites_an_existing_attempt(self) -> None:
        original = _core(bad_key_results=False, bad_boundary=False)
        result = anchor_repair.recover_core_anchors_once(
            original,
            paper_id=PAPER_ID,
            chunk_texts=_chunks(),
            prompt_body="BUNDLE\n__ANCHOR_REPAIR_BUNDLE_JSON__",
            prompt_version="v1",
            call_repair=lambda _prompt: self.fail("repair call must not run"),
        )
        with tempfile.TemporaryDirectory() as temp:
            first = anchor_repair.write_trace_exclusive(Path(temp), PAPER_ID, result)
            first_bytes = first.read_bytes()
            second = anchor_repair.write_trace_exclusive(Path(temp), PAPER_ID, result)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), first_bytes)

    def test_distill_integration_writes_trace_and_runs_final_validator(self) -> None:
        original = _core()

        def response(prompt: str, _max_tokens: int):
            bundle_text = prompt.rsplit("Frozen repair bundle:\n\n", 1)[1]
            bundle = _wire_bundle(prompt, bundle_text)
            repairs = []
            for target in bundle["targets"]:
                repairs.append({
                    "field": target["field"],
                    "anchor_index": target["anchor_index"],
                    "bad_chunk_id": target["bad_chunk_id"],
                    "action": "replace",
                    "replacement_anchors": [
                        {"chunk_id": target["allowed_chunk_ids"][0], "quote": None}
                    ],
                    "reason": "Use a frozen same-paper candidate.",
                })
            return {
                "text": json.dumps({
                    "paper_id": bundle["paper_id"],
                    "authoritative_sha256": bundle["authoritative_sha256"],
                    "repairs": repairs,
                }),
                "raw": {"choices": [{"finish_reason": "stop"}]},
            }

        records = [
            {
                "chunk_id": chunk_id,
                "text": text,
                "section_path": "Results",
                "page_start": 1,
                "page_end": 1,
            }
            for chunk_id, text in _chunks().items()
        ]
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(distill, "_call_distill", side_effect=response) as model_call,
                patch.object(distill, "log"),
                patch.object(distill, "log_error"),
            ):
                repaired, trace, status = distill._repair_core_anchors_if_needed(
                    copy.deepcopy(original),
                    folder=Path(temp),
                    paper_id=PAPER_ID,
                ownership="self",
                chunk_texts=_chunks(),
                candidate_chunk_ids=set(_chunks()) - {f"{PAPER_ID}#c0039"},
            )
                fields, by_field = distill._validate_and_build(
                    repaired,
                    records,
                    _chunks(),
                    PAPER_ID,
                )

        self.assertEqual(status, "repaired")
        self.assertEqual(model_call.call_count, 1)
        self.assertTrue(trace)
        self.assertEqual(fields["key_results"], original["key_results"]["value"])
        self.assertTrue(by_field["boundary_conditions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


def _wire_bundle(prompt, text):
    bundle, _ = json.JSONDecoder().raw_decode(text.lstrip())
    if 'frozen_field_values' in bundle:
        source_text = prompt.split('<SOURCE_CONTEXT>\n', 1)[1].split('\n</SOURCE_CONTEXT>', 1)[0]
        sources = {row['chunk_id']: row for row in json.loads(source_text)}
        for target in bundle['targets']:
            target['field_value'] = bundle['frozen_field_values'][target['field']]
            target['candidate_sources'] = [sources[cid] for cid in target['candidate_source_ids']]
    return bundle
