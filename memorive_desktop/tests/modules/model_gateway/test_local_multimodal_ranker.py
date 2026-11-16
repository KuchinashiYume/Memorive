from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.research.retrieval.retrieval_calibration.candidate_features import build_reranker_provenance
from memorive.models.gateway.local_multimodal_ranker import (  # noqa: E402
    LocalMultimodalRankerAdapter,
    LocalMultimodalRankerViolation,
    build_request,
)


Retrieval_SHA = "A" * 64
DIGEST = "25b843619e94" + "0" * 52


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _binding(endpoint: str = "http://127.0.0.1:11434/api/chat") -> dict:
    return {
        "schema_version": "RetrievalCalibrationLocalMultimodalRankerBinding-v1",
        "endpoint": endpoint,
        "requested_model": "qwen3.8:27b-q4_K_M",
        "expected_model_digest_prefix": DIGEST[:12],
        "think": False,
        "temperature": 0,
        "seed": 20260820,
        "num_predict": 512,
        "max_candidates_per_query": 8,
        "cloud_fallback": False,
        "dedicated_reranker_fallback": False,
    }


def _batch() -> dict:
    documents = []
    for candidate_id, role, text in (
        ("A", "methods", "Samples were incubated at 37 C for 20 minutes."),
        ("B", "references", "References: Incubation methods and duration."),
        ("C", "body", "A temperature-controlled step preceded measurement."),
    ):
        documents.append(
            {
                "candidate_id": candidate_id,
                "text": text,
                "provenance": build_reranker_provenance(
                    {
                        "source_role": role,
                        "page_family": "digital",
                        "metadata_status": "vector",
                    },
                    retrieval_terminal_sha256=Retrieval_SHA,
                ),
            }
        )
    return {
        "query_id": "Q1::RERANK_ONLY_SHADOW",
        "query": "At what temperature and for how long?",
        "query_intent": "method_condition",
        "documents": documents,
    }


class LocalMultimodalRankerTests(unittest.TestCase):
    def test_contract_is_gold_free_and_loopback_only(self) -> None:
        request = build_request([_batch()], _binding())
        self.assertEqual(request["ranking_mode"], "GENERATIVE_LISTWISE_EXACT_SET")
        self.assertFalse(request["synthetic_relevance_scores"])
        with self.assertRaisesRegex(LocalMultimodalRankerViolation, "NON_LOOPBACK"):
            build_request([_batch()], _binding("https://example.com/api/chat"))
        contaminated = _batch()
        contaminated["gold"] = {"A": 3}
        with self.assertRaisesRegex(LocalMultimodalRankerViolation, "EVALUATOR_DATA"):
            build_request([contaminated], _binding())

    def test_adapter_returns_complete_order_without_scores(self) -> None:
        calls = []
        posted_body = {}

        def fake_open(request, *, timeout):
            calls.append((request.full_url, request.get_method(), timeout))
            if request.get_method() == "GET":
                payload = {"models": [{"model": _binding()["requested_model"], "digest": DIGEST}]}
            else:
                posted_body.update(json.loads(request.data.decode("utf-8")))
                payload = {
                    "model": _binding()["requested_model"],
                    "message": {
                        "content": json.dumps(
                            {
                                "rankings": [
                                    {
                                        "query_id": "Q1::RERANK_ONLY_SHADOW",
                                        "candidate_ids": ["A", "C", "B"],
                                    }
                                ]
                            }
                        )
                    },
                    "prompt_eval_count": 100,
                    "eval_count": 20,
                }
            return _Response(json.dumps(payload).encode("utf-8"))

        outcome = LocalMultimodalRankerAdapter(urlopen=fake_open).execute([_batch()], _binding())
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.rankings[0]["candidate_ids"], ["A", "C", "B"])
        self.assertNotIn("scores", outcome.rankings[0])
        self.assertEqual(outcome.receipt["external_request_count"], 0)
        self.assertEqual(outcome.receipt["dedicated_reranker_fallback_count"], 0)
        self.assertEqual(outcome.receipt["keep_alive"], 0)
        self.assertEqual([row[1] for row in calls], ["GET", "POST"])
        self.assertEqual(posted_body["keep_alive"], 0)

    def test_duplicate_or_missing_candidate_invalidates_whole_result(self) -> None:
        def fake_open(request, *, timeout):
            if request.get_method() == "GET":
                payload = {"models": [{"model": _binding()["requested_model"], "digest": DIGEST}]}
            else:
                payload = {
                    "model": _binding()["requested_model"],
                    "message": {
                        "content": json.dumps(
                            {
                                "rankings": [
                                    {
                                        "query_id": "Q1::RERANK_ONLY_SHADOW",
                                        "candidate_ids": ["A", "A", "B"],
                                    }
                                ]
                            }
                        )
                    },
                }
            return _Response(json.dumps(payload).encode("utf-8"))

        outcome = LocalMultimodalRankerAdapter(urlopen=fake_open).execute([_batch()], _binding())
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_code, "RANKING_CANDIDATE_EXACT_SET_INVALID")
        self.assertEqual(outcome.rankings, ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
