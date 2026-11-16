from __future__ import annotations

import io
import json
import unittest
import urllib.error
from pathlib import Path
from typing import Any

from memorive.research.discovery.live_metadata_transport import LiveTransportError, TransportPolicy, canonical_sha256, sha256_bytes
from memorive.research.discovery.live_metadata_transport import ReceiptValidator, build_pre_send_receipt, build_url, execute_get
# The repository's own Discovery contracts.
CONTRACT_ROOT = Path(__file__).resolve().parents[4] / "contracts" / "Discovery_文献发现" / "10_模块契约"
SCHEMA_PATH = next(CONTRACT_ROOT.glob("*_ExternalSourceRequestReceipt_*r0.2.schema.json"))
H = "A" * 64


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        url: str = "https://api.crossref.org/works?query=cnn&rows=2",
    ) -> None:
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self.url = url

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def base(
    provider: str = "CROSSREF",
    *,
    max_bytes: int = 1024,
    max_records: int = 2,
    retry_ceiling: int = 0,
):
    policy = TransportPolicy(max_response_bytes=max_bytes, max_records=max_records)
    query = (
        {"query": "cnn", "rows": max_records}
        if provider == "CROSSREF"
        else {"search_query": "all:cnn", "start": 0, "max_results": max_records}
    )
    receipt = build_pre_send_receipt(
        recorded_at="2026-08-12T00:00:00+09:00",
        run_id="TRANSPORT_TEST_RUN",
        attempt_id="A1-CROSSREF-CONNECTIVITY",
        external_call_id="extcall_0123456789abcdef01234567",
        request_attempt_id="extattempt_0123456789abcdef01234567",
        retry_of_request_attempt_id=None,
        provider=provider,
        operation="CONNECTIVITY",
        query=query,
        policy=policy,
        profile_identity={
            "profile_id": "TRANSPORT_TEST_PROFILE",
            "profile_version": "1",
            "profile_hash": H,
            "identity_status": "VERIFIED",
        },
        route_identity={
            "route_id": "ROUTE_TEST",
            "route_snapshot_id": "ROUTE_SNAPSHOT_TEST",
            "provider_region": "UNSPECIFIED_PROVIDER_EDGE",
            "egress_identity": "TEST_ONLY_OPAQUE",
            "egress_identity_kind": "OPAQUE_LABEL",
            "egress_receipt_id": "EGRESS_TEST",
            "identity_status": "RECORDED_UNVERIFIED",
        },
        artifact_bindings={
            "source_snapshot_id": "SNAPSHOT_TEST",
            "source_commit": "a" * 40,
            "source_tree_hash": H,
            "runner_hash": H,
            "prompt_hash": H,
            "output_contract_hash": H,
            "scorer_hash": None,
            "input_manifest_hash": H,
            "input_hash": canonical_sha256(query),
            "output_hash": None,
            "error_hash": None,
        },
        authorization={
            "status": "AUTHORIZED_EXACT_REQUEST",
            "authorization_id": "AUTH_TEST",
            "authorization_text_sha256": H,
        },
        terms_snapshot={
            "status": "CURRENT_OFFICIAL_CHECKED",
            "source_urls": ["https://example.test/terms"],
            "checked_at": "2026-08-12T00:00:00+09:00",
            "content_sha256": H,
        },
        source_evidence_refs=[
            {"ref": "test://manifest", "sha256": H, "evidence_status": "EXACT_RECORDED"}
        ],
        retry_ceiling=retry_ceiling,
    )
    return policy, query, receipt


class LiveTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._temp = tempfile.TemporaryDirectory()
        self.temp = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_schema_and_pre_send_positive(self) -> None:
        _, _, receipt = base()
        ReceiptValidator(SCHEMA_PATH).validate(receipt, pre_send=True)

    def test_arxiv_quoted_phrase_percent_encodes_double_quotes(self) -> None:
        policy = TransportPolicy(max_records=20)
        url = build_url(
            "ARXIV",
            {
                "search_query": 'all:"image classification" AND all:convolutional',
                "start": 0,
                "max_results": 20,
            },
            policy,
        )
        self.assertIn("all:%22image+classification%22+AND+all:convolutional", url)
        self.assertNotIn('"', url)

    def test_task_and_run_binding_drift_block_before_opener(self) -> None:
        for field in ("task_id", "run_id"):
            with self.subTest(field=field):
                policy, query, receipt = base()
                receipt[field] = "DRIFT"
                receipt["content_hash"] = canonical_sha256(
                    {key: value for key, value in receipt.items() if key != "content_hash"}
                )
                calls = [0]

                def opener(*_args: Any) -> FakeResponse:
                    calls[0] += 1
                    return FakeResponse(b"{}")

                with self.assertRaises(LiveTransportError):
                    execute_get(
                        provider="CROSSREF",
                        query=query,
                        pre_send_receipt=receipt,
                        response_path=self.temp / f"{field}.json",
                        receipt_validator=ReceiptValidator(SCHEMA_PATH),
                        parser=lambda raw: {"records": []},
                        policy=policy,
                        opener=opener,
                        timestamp=lambda: "2026-08-12T00:00:01+09:00",
                        expected_run_id="TRANSPORT_TEST_RUN",
                    )
                self.assertEqual(calls[0], 0)

    def test_request_hash_drift_blocks_before_opener(self) -> None:
        policy, _, receipt = base()
        calls = [0]

        def opener(*_args: Any) -> FakeResponse:
            calls[0] += 1
            return FakeResponse(b"{}")

        with self.assertRaisesRegex(LiveTransportError, "request binding mismatch"):
            execute_get(
                provider="CROSSREF",
                query={"query": "different", "rows": 2},
                pre_send_receipt=receipt,
                response_path=self.temp / "raw.json",
                receipt_validator=ReceiptValidator(SCHEMA_PATH),
                parser=lambda raw: {"records": []},
                policy=policy,
                opener=opener,
                timestamp=lambda: "2026-08-12T00:00:01+09:00",
            )
        self.assertEqual(calls[0], 0)

    def test_success_persists_before_parse_and_finalizes(self) -> None:
        policy, query, receipt = base()
        raw = json.dumps({"message": {"items": [{"DOI": "10.1/A"}]}}).encode()
        response_path = self.temp / "raw.json"
        seen = [False]

        def parser(value: bytes):
            seen[0] = response_path.is_file() and response_path.read_bytes() == value
            return {"records": json.loads(value)["message"]["items"]}

        terminal, parsed = execute_get(
            provider="CROSSREF",
            query=query,
            pre_send_receipt=receipt,
            response_path=response_path,
            receipt_validator=ReceiptValidator(SCHEMA_PATH),
            parser=parser,
            policy=policy,
            opener=lambda *_: FakeResponse(raw),
            timestamp=lambda: "2026-08-12T00:00:01+09:00",
        )
        self.assertTrue(seen[0])
        self.assertEqual(parsed, {"records": [{"DOI": "10.1/A"}]})
        self.assertEqual(terminal["call_status"], "SUCCESS")
        self.assertEqual(terminal["response"]["raw_response_bytes"], len(raw))
        self.assertEqual(
            terminal["qualification_eligibility"], "ELIGIBLE_FOR_Integration_SOURCE_REPLAY"
        )

    def test_parser_error_preserves_raw_response(self) -> None:
        policy, query, receipt = base()
        raw = b'{"message":{"items":[]}}'
        path = self.temp / "raw.json"

        def parser(_raw: bytes):
            raise ValueError("synthetic parser failure")

        terminal, parsed = execute_get(
            provider="CROSSREF",
            query=query,
            pre_send_receipt=receipt,
            response_path=path,
            receipt_validator=ReceiptValidator(SCHEMA_PATH),
            parser=parser,
            policy=policy,
            opener=lambda *_: FakeResponse(raw),
            timestamp=lambda: "2026-08-12T00:00:01+09:00",
        )
        self.assertIsNone(parsed)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(terminal["call_status"], "ERROR")
        self.assertEqual(terminal["parser"]["status"], "ERROR")
        self.assertIsNotNone(terminal["response"]["raw_response_sha256"])

    def test_transport_failures_are_receipted(self) -> None:
        cases = [
            (FakeResponse(b"x" * 1025), "RESPONSE_TOO_LARGE"),
            (FakeResponse(b"{}", content_type="text/html"), "CONTENT_TYPE_NOT_ALLOWED"),
            (FakeResponse(b"{}", url="https://evil.example/works"), "REDIRECT_NOT_ALLOWED"),
            (FakeResponse(b"{}", status=429), "HTTP_429"),
            (FakeResponse(b"{}", status=503), "HTTP_503"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                policy, query, receipt = base()
                terminal, parsed = execute_get(
                    provider="CROSSREF",
                    query=query,
                    pre_send_receipt=receipt,
                    response_path=self.temp / f"{expected}.bin",
                    receipt_validator=ReceiptValidator(SCHEMA_PATH),
                    parser=lambda raw: {"records": []},
                    policy=policy,
                    opener=lambda *_args, value=response: value,
                    timestamp=lambda: "2026-08-12T00:00:01+09:00",
                )
                self.assertIsNone(parsed)
                self.assertEqual(terminal["error_code"], expected)
                self.assertEqual(
                    terminal["qualification_eligibility"],
                    "NOT_ELIGIBLE_FOR_Integration_SOURCE_REPLAY",
                )

    def test_http_error_retries_only_transient_statuses(self) -> None:
        cases = [(404, "RETRY_DENIED"), (429, "RETRY_ALLOWED_TRANSIENT"), (503, "RETRY_ALLOWED_TRANSIENT")]
        for status, expected_disposition in cases:
            with self.subTest(status=status):
                policy, query, receipt = base(retry_ceiling=1)
                body = f"synthetic HTTP {status}".encode("utf-8")

                def opener(request, _timeout, *, code=status):
                    raise urllib.error.HTTPError(
                        request.full_url,
                        code,
                        "synthetic",
                        {"Content-Type": "application/json"},
                        io.BytesIO(body),
                    )

                terminal, parsed = execute_get(
                    provider="CROSSREF",
                    query=query,
                    pre_send_receipt=receipt,
                    response_path=self.temp / f"HTTP_{status}.bin",
                    receipt_validator=ReceiptValidator(SCHEMA_PATH),
                    parser=lambda raw: {"records": []},
                    policy=policy,
                    opener=opener,
                    timestamp=lambda: "2026-08-12T00:00:01+09:00",
                )
                self.assertIsNone(parsed)
                self.assertEqual(terminal["error_code"], f"HTTP_{status}")
                self.assertEqual(terminal["response"]["protocol_status"], status)
                self.assertEqual(terminal["retry_disposition"], expected_disposition)
                self.assertEqual(terminal["response"]["raw_response_bytes"], len(body))
                self.assertEqual(terminal["response"]["raw_response_sha256"], sha256_bytes(body))
                self.assertEqual(
                    Path(terminal["response"]["response_path"]).read_bytes(),
                    body,
                )
                self.assertIsNone(terminal["response"]["source_schema_revision"])

    def test_oversized_http_error_body_is_terminal_not_uncaught(self) -> None:
        policy, query, receipt = base(max_bytes=8, retry_ceiling=1)

        def opener(request, _timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "synthetic",
                {"Content-Type": "application/json"},
                io.BytesIO(b"123456789"),
            )

        terminal, parsed = execute_get(
            provider="CROSSREF",
            query=query,
            pre_send_receipt=receipt,
            response_path=self.temp / "oversized-error.bin",
            receipt_validator=ReceiptValidator(SCHEMA_PATH),
            parser=lambda raw: {"records": []},
            policy=policy,
            opener=opener,
            timestamp=lambda: "2026-08-12T00:00:01+09:00",
        )
        self.assertIsNone(parsed)
        self.assertEqual(terminal["error_code"], "RESPONSE_TOO_LARGE")
        self.assertEqual(terminal["call_status"], "ERROR")
        self.assertIsNone(terminal["response"]["raw_response_sha256"])
        self.assertIsNone(terminal["response"]["response_path"])
        self.assertFalse((self.temp / "oversized-error.bin").exists())

    def test_http_error_without_entity_is_hash_bound_as_empty_bytes(self) -> None:
        policy, query, receipt = base(retry_ceiling=0)

        def opener(request, _timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "synthetic no entity",
                {"Content-Type": "application/json"},
                None,
            )

        target = self.temp / "empty-error.bin"
        terminal, parsed = execute_get(
            provider="CROSSREF",
            query=query,
            pre_send_receipt=receipt,
            response_path=target,
            receipt_validator=ReceiptValidator(SCHEMA_PATH),
            parser=lambda raw: {"records": []},
            policy=policy,
            opener=opener,
            timestamp=lambda: "2026-08-12T00:00:01+09:00",
        )
        self.assertIsNone(parsed)
        self.assertEqual(terminal["error_code"], "HTTP_404")
        self.assertEqual(terminal["response"]["raw_response_bytes"], 0)
        self.assertEqual(terminal["response"]["raw_response_sha256"], sha256_bytes(b""))
        self.assertEqual(target.read_bytes(), b"")

    def test_arbitrary_url_cannot_be_supplied(self) -> None:
        policy, query, _ = base()
        with self.assertRaises(TypeError):
            build_pre_send_receipt(  # type: ignore[call-arg]
                recorded_at="2026-08-12T00:00:00+09:00",
                run_id="x",
                attempt_id="x",
                external_call_id="extcall_0123456789abcdef01234567",
                request_attempt_id="extattempt_0123456789abcdef01234567",
                retry_of_request_attempt_id=None,
                provider="CROSSREF",
                operation="CONNECTIVITY",
                query=query,
                policy=policy,
                profile_identity={},
                route_identity={},
                artifact_bindings={},
                authorization={},
                terms_snapshot={},
                source_evidence_refs=[],
                url="http://evil.example",
            )


if __name__ == "__main__":
    unittest.main()
