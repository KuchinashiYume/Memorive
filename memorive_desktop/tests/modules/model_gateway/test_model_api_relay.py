from __future__ import annotations

import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest import mock
import urllib.error

import jsonschema

from model_gateway.config import GatewayConfig
from model_gateway.model_api_relay import AttemptLedger, EndpointProfileCatalog, ProfiledRouter, RelayContractError, RelayProtocolRegistry, RelayRoutePlanner, SafeRelayClient, build_capability_loss_map, build_compatibility_report, build_presend_blocked_receipt, build_relay_qualification_key, build_relay_attempt_receipt, canonical_sha256, classify_backend_identity, evaluate_qualification, prepare_public_synthetic_ocr, project_runtime_log_relay_extension, validate_credential_binding, validate_endpoint_profile, validate_model_alias_binding, validate_protocol_manifest
HASH_A = "A" * 64
HASH_B = "B" * 64
HASH_C = "C" * 64
NOW = "2026-08-15T00:00:00Z"


def profile(**overrides):
    value = {
        "schema_version": "MODEL_API_RELAY_MODEL_API_ENDPOINT_PROFILE_V1",
        "profile_id": "relay-openai-chat-v1",
        "revision": "r1",
        "status": "QUALIFIED_DISABLED",
        "transport_kind": "MODEL_API_RELAY",
        "relay_mode": "PROTOCOL_PRESERVING",
        "access_mode": "API_VIA_RELAY",
        "protocol_family": "OPENAI_CHAT_COMPLETIONS",
        "protocol_revision": "2024-01",
        "capabilities": ["chat"],
        "allowed_roles": ["distill"],
        "endpoint_operator": "ExampleRelay",
        "claimed_upstream_provider": "DeepSeek",
        "scheme": "https",
        "origin": "https://relay.example.test",
        "path_template": "/v1/chat/completions",
        "allowed_query_keys": [],
        "redirect_policy": "DENY",
        "allowed_origins": ["https://relay.example.test"],
        "credential_binding_id": "relay-example-key-v1",
        "credential_binding_revision": "r1",
        "credential_ref": "MODEL_API_RELAY_RELAY_KEY",
        "auth_scheme": "Bearer",
        "header_name": "Authorization",
        "logical_model_id": "distill-low-cost",
        "request_model_alias": "deepseek-v4-flash",
        "alias_revision": "r1",
        "route_id": "relay-openai-chat-v1",
        "region": "JP",
        "egress": "egress-profile-a",
        "dns_proxy_profile": "system-proxy-a",
        "billing_mode": "paygo_api",
        "usage_source": "relay_response",
        "cost_source": "operator_estimate",
        "currency": "USD",
        "behavior_hash": HASH_A,
        "request_contract_hash": HASH_B,
        "output_contract_hash": HASH_C,
        "qualification_report_ref": "sandbox:qualification/relay-openai-chat-v1.json",
        "valid_from": "2026-08-14T00:00:00Z",
        "expires_at": "2026-08-20T00:00:00Z",
        "revocation_conditions": ["MODEL_ALIAS_DRIFT", "ORIGIN_DRIFT"],
        "expected_embedding_dimension": None,
        "rerank_score_range": None,
    }
    value.update(overrides)
    return value


def binding(**overrides):
    value = {
        "schema_version": "MODEL_API_RELAY_CREDENTIAL_ENDPOINT_BINDING_V1",
        "credential_binding_id": "relay-example-key-v1",
        "revision": "r1",
        "credential_ref": "MODEL_API_RELAY_RELAY_KEY",
        "binding_kind": "relay",
        "endpoint_operator": "ExampleRelay",
        "allowed_origin": "https://relay.example.test",
        "auth_scheme": "Bearer",
        "header_name": "Authorization",
    }
    value.update(overrides)
    return value


def alias(**overrides):
    value = {
        "schema_version": "MODEL_API_RELAY_MODEL_ALIAS_BINDING_V1",
        "logical_model_id": "distill-low-cost",
        "request_model_alias": "deepseek-v4-flash",
        "alias_revision": "r1",
        "endpoint_operator": "ExampleRelay",
        "claimed_upstream_provider": "DeepSeek",
        "identity_threshold": "RELAY_REPORTED_UNVERIFIED",
        "fallback_aliases": [],
        "pool_identity": None,
        "selection_policy": "EXACT_MODEL",
    }
    value.update(overrides)
    return value


def manifest(**overrides):
    value = {
        "schema_version": "MODEL_API_RELAY_PROTOCOL_ADAPTER_MANIFEST_V1",
        "adapter_id": "openai-chat-v1",
        "protocol_family": "OPENAI_CHAT_COMPLETIONS",
        "protocol_revision": "2024-01",
        "capabilities": ["chat"],
        "request_contract_hash": HASH_B,
        "output_contract_hash": HASH_C,
        "max_response_bytes": 65536,
        "streaming": False,
        "relay_mode": "PROTOCOL_PRESERVING",
        "allowed_headers": ["anthropic-version", "anthropic-beta"],
    }
    value.update(overrides)
    return value


def catalog_for(p=None, b=None, a=None, m=None):
    return EndpointProfileCatalog(
        profiles=[p or profile()],
        credential_bindings=[b or binding()],
        model_alias_bindings=[a or alias()],
        protocol_manifests=[m or manifest()],
    )


class ContractValidationTests(unittest.TestCase):
    def test_endpoint_profile_is_canonical_and_fingerprint_bound(self):
        accepted = validate_endpoint_profile(profile())
        self.assertEqual(accepted["profile_id"], "relay-openai-chat-v1")
        self.assertRegex(accepted["profile_fingerprint"], r"^[0-9A-F]{64}$")
        changed = validate_endpoint_profile(profile(endpoint_operator="OtherRelay"))
        self.assertNotEqual(accepted["profile_fingerprint"], changed["profile_fingerprint"])

    def test_profile_rejects_remote_http_userinfo_query_and_private_origin(self):
        cases = [
            profile(scheme="http", origin="http://relay.example.test"),
            profile(origin="https://user@relay.example.test"),
            profile(origin="https://relay.example.test?token=x"),
            profile(origin="https://127.0.0.1"),
        ]
        for value in cases:
            with self.subTest(value=value["origin"]):
                with self.assertRaises(RelayContractError):
                    validate_endpoint_profile(value)

    def test_loopback_http_requires_self_hosted_transport(self):
        value = profile(
            transport_kind="SELF_HOSTED_LOOPBACK_RELAY",
            endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY",
            scheme="http",
            origin="http://127.0.0.1:43123",
            allowed_origins=["http://127.0.0.1:43123"],
        )
        self.assertEqual(validate_endpoint_profile(value)["scheme"], "http")

    def test_credential_binding_rejects_secret_value_and_origin_drift(self):
        self.assertEqual(validate_credential_binding(binding())["credential_ref"], "MODEL_API_RELAY_RELAY_KEY")
        with self.assertRaises(RelayContractError):
            validate_credential_binding(binding(credential_ref="sk-secret-material"))
        with self.assertRaises(RelayContractError):
            validate_credential_binding(binding(allowed_origin="https://other.example.test"), profile())

    def test_gemini_cli_oauth_session_cannot_be_reused_as_api_credential(self):
        with self.assertRaisesRegex(RelayContractError, "AUTH_SCHEME_INVALID"):
            validate_credential_binding(
                binding(
                    credential_ref="GEMINI_CLI_OAUTH_SESSION",
                    auth_scheme="OAuthSession",
                )
            )

    def test_alias_and_protocol_manifest_fail_closed_on_extra_fields(self):
        self.assertEqual(validate_model_alias_binding(alias())["alias_revision"], "r1")
        self.assertEqual(validate_protocol_manifest(manifest())["adapter_id"], "openai-chat-v1")
        with self.assertRaises(RelayContractError):
            validate_model_alias_binding({**alias(), "secret": "forbidden"})
        with self.assertRaises(RelayContractError):
            validate_protocol_manifest({**manifest(), "unknown": True})


class CatalogAndRoutingTests(unittest.TestCase):
    def catalog(self, value=None):
        return EndpointProfileCatalog(
            profiles=[value or profile()],
            credential_bindings=[binding()],
            model_alias_bindings=[alias()],
            protocol_manifests=[manifest()],
        )

    def test_catalog_resolves_exact_role_capability_and_time(self):
        resolved = self.catalog().resolve(
            "relay-openai-chat-v1", role="distill", capability="chat", at=NOW
        )
        self.assertEqual(resolved.profile["endpoint_operator"], "ExampleRelay")
        for role, capability, at in [
            ("analysis", "chat", NOW),
            ("distill", "embedding", NOW),
            ("distill", "chat", "2026-08-21T00:00:00Z"),
        ]:
            with self.subTest(role=role, capability=capability, at=at):
                with self.assertRaises(RelayContractError):
                    self.catalog().resolve(
                        "relay-openai-chat-v1", role=role, capability=capability, at=at
                    )

    def test_catalog_rejects_duplicates_hash_drift_and_disabled_profile(self):
        with self.assertRaises(RelayContractError):
            EndpointProfileCatalog(
                profiles=[profile(), profile()],
                credential_bindings=[binding()],
                model_alias_bindings=[alias()],
                protocol_manifests=[manifest()],
            )
        disabled = profile(status="DISABLED")
        with self.assertRaises(RelayContractError):
            self.catalog(disabled).resolve(
                disabled["profile_id"], role="distill", capability="chat", at=NOW
            )

    def test_route_planner_never_silently_falls_back(self):
        planner = RelayRoutePlanner(
            {"distill": {"direct": "direct-deepseek", "relay": "relay-openai-chat-v1"}}
        )
        self.assertEqual(planner.select("distill", mode="relay-only"), "relay-openai-chat-v1")
        self.assertEqual(planner.select("distill", mode="direct-only"), "direct-deepseek")
        with self.assertRaises(RelayContractError):
            planner.on_failure("relay-openai-chat-v1", fallback_requested=True)
        self.assertEqual(planner.rollback("distill"), "direct-deepseek")


class ProtocolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = RelayProtocolRegistry()

    def test_openai_chat_request_and_response(self):
        request = self.registry.build_request(
            "OPENAI_CHAT_COMPLETIONS",
            profile(),
            {"messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 8},
        )
        self.assertEqual(request.path, "/v1/chat/completions")
        self.assertEqual(request.body["model"], "deepseek-v4-flash")
        result = self.registry.parse_response(
            "OPENAI_CHAT_COMPLETIONS",
            profile(),
            200,
            "application/json",
            json.dumps({
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }).encode(),
        )
        self.assertEqual(result["text"], "OK")
        self.assertEqual(result["usage"]["total_tokens"], 3)

    def test_responses_anthropic_and_gemini_are_supported(self):
        cases = [
            (
                "OPENAI_RESPONSES",
                profile(protocol_family="OPENAI_RESPONSES", path_template="/v1/responses", capabilities=["chat_structured_tools"]),
                {"input": "Reply OK", "max_output_tokens": 8},
                {"model": "gpt-4.1-mini", "output_text": "OK", "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}},
            ),
            (
                "ANTHROPIC_MESSAGES",
                profile(protocol_family="ANTHROPIC_MESSAGES", path_template="/v1/messages"),
                {"messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 8},
                {"model": "claude-haiku-4-5", "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 1}},
            ),
            (
                "GEMINI_GENERATE_CONTENT",
                profile(protocol_family="GEMINI_GENERATE_CONTENT", path_template="/v1beta/models/{model}:generateContent"),
                {"contents": [{"role": "user", "parts": [{"text": "Reply OK"}]}], "max_output_tokens": 8},
                {"modelVersion": "gemini-3-pro-preview", "candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3}},
            ),
        ]
        for protocol, p, payload, response in cases:
            with self.subTest(protocol=protocol):
                built = self.registry.build_request(protocol, p, payload)
                self.assertTrue(built.path)
                parsed = self.registry.parse_response(protocol, p, 200, "application/json", json.dumps(response).encode())
                self.assertEqual(parsed["text"], "OK")

    def test_embedding_count_order_dimension_are_exact(self):
        p = profile(protocol_family="OPENAI_EMBEDDINGS", path_template="/v1/embeddings", capabilities=["embedding"], expected_embedding_dimension=2)
        body = {"model": "BAAI/bge-m3", "data": [{"index": 0, "embedding": [0.1, 0.2]}, {"index": 1, "embedding": [0.3, 0.4]}], "usage": {"prompt_tokens": 2, "total_tokens": 2}}
        parsed = self.registry.parse_response("OPENAI_EMBEDDINGS", p, 200, "application/json", json.dumps(body).encode(), expected_count=2)
        self.assertEqual(parsed["dimension"], 2)
        for bad in [
            {**body, "data": [body["data"][1], body["data"][0]]},
            {**body, "data": [{"index": 0, "embedding": [0.1]}, body["data"][1]]},
        ]:
            with self.assertRaises(RelayContractError):
                self.registry.parse_response("OPENAI_EMBEDDINGS", p, 200, "application/json", json.dumps(bad).encode(), expected_count=2)

    def test_rerank_indices_scores_and_usage_fail_closed(self):
        p = profile(protocol_family="PROVIDER_RERANK", path_template="/v1/rerank", capabilities=["rerank"], rerank_score_range=[0.0, 1.0])
        good = {"model": "BAAI/bge-reranker-v2-m3", "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}], "usage": {"prompt_tokens": 3, "total_tokens": 3}}
        parsed = self.registry.parse_response("PROVIDER_RERANK", p, 200, "application/json", json.dumps(good).encode(), expected_count=2)
        self.assertEqual(len(parsed["results"]), 2)
        bad = copy.deepcopy(good)
        bad["results"][1]["index"] = 0
        with self.assertRaises(RelayContractError):
            self.registry.parse_response("PROVIDER_RERANK", p, 200, "application/json", json.dumps(bad).encode(), expected_count=2)

    def test_error_content_type_size_and_negative_usage_are_classified(self):
        p = profile()
        with self.assertRaisesRegex(RelayContractError, "HTTP_429"):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", p, 429, "application/json", b'{"error":{"message":"busy"}}')
        with self.assertRaisesRegex(RelayContractError, "CONTENT_TYPE_INVALID"):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", p, 200, "text/html", b"OK")
        negative = {"model":"x","choices":[{"message":{"content":"OK"}}],"usage":{"prompt_tokens":-1,"completion_tokens":1,"total_tokens":0}}
        with self.assertRaisesRegex(RelayContractError, "USAGE_INVALID"):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", p, 200, "application/json", json.dumps(negative).encode())

    def test_openai_anthropic_responses_and_gemini_stream_terminals(self):
        streams = [
            ("OPENAI_CHAT_COMPLETIONS", profile(), "data: {\"model\":\"deepseek-v4-flash\",\"choices\":[{\"delta\":{\"content\":\"O\"},\"finish_reason\":null}]}\n\ndata: {\"model\":\"deepseek-v4-flash\",\"choices\":[{\"delta\":{\"content\":\"K\"},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":1,\"total_tokens\":3}}\n\ndata: [DONE]\n\n"),
            ("ANTHROPIC_MESSAGES", profile(protocol_family="ANTHROPIC_MESSAGES", path_template="/v1/messages"), "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"model\":\"claude-haiku-4-5\",\"usage\":{\"input_tokens\":2}}}\n\nevent: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"OK\"}}\n\nevent: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":1}}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"),
            ("OPENAI_RESPONSES", profile(protocol_family="OPENAI_RESPONSES", path_template="/v1/responses"), "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"OK\"}\n\nevent: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"model\":\"gpt-4.1-mini\",\"status\":\"completed\",\"usage\":{\"input_tokens\":2,\"output_tokens\":1,\"total_tokens\":3}}}\n\n"),
            ("GEMINI_GENERATE_CONTENT", profile(protocol_family="GEMINI_GENERATE_CONTENT", path_template="/v1beta/models/{model}:streamGenerateContent"), "data: {\"modelVersion\":\"gemini-3-pro-preview\",\"candidates\":[{\"content\":{\"parts\":[{\"text\":\"OK\"}]},\"finishReason\":\"STOP\"}],\"usageMetadata\":{\"promptTokenCount\":2,\"candidatesTokenCount\":1,\"totalTokenCount\":3}}\n\n"),
        ]
        for protocol, p, stream in streams:
            with self.subTest(protocol=protocol):
                parsed = self.registry.parse_response(protocol, p, 200, "text/event-stream", stream.encode())
                self.assertEqual(parsed["text"], "OK")
                self.assertEqual(parsed["usage"]["total_tokens"], 3)
        with self.assertRaises(RelayContractError):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", profile(), 200, "text/event-stream", b'data: {"model":"x","choices":[]}\n\n')


class EvidenceAndIntegrationTests(unittest.TestCase):
    def test_relay_receipt_separates_operator_upstream_identity_and_billing(self):
        receipt = build_relay_attempt_receipt(
            attempt_id="attempt-001",
            profile=validate_endpoint_profile(profile()),
            requested_model="deepseek-v4-flash",
            relay_reported_model="deepseek-v4-flash",
            returned_model="deepseek-v4-flash",
            independent_backend_identity=None,
            route="relay-openai-chat-v1",
            region="JP",
            egress="egress-profile-a",
            behavior_hash=HASH_A,
            input_hash=HASH_B,
            output_hash=HASH_C,
            latency_ms=12.5,
            usage={"input_tokens": 2, "output_tokens": 1, "reasoning_tokens": 0, "total_tokens": 3},
            operator_billing={"status": "ESTIMATED", "value": 0.0001, "currency": "USD"},
            upstream_billing={"status": "UNKNOWN", "value": None, "currency": None},
        )
        self.assertEqual(receipt["endpoint_operator"], "ExampleRelay")
        self.assertEqual(receipt["claimed_upstream_provider"], "DeepSeek")
        self.assertEqual(receipt["identity_grade"], "RELAY_REPORTED_UNVERIFIED")
        self.assertNotIn("credential", json.dumps(receipt).lower())
        extension = project_runtime_log_relay_extension(receipt)
        self.assertEqual(extension["schema_version"], "MODEL_API_RELAY_RUNTIME_LOG_RELAY_EXTENSION_V1")

    def test_reasoning_tokens_are_not_added_twice(self):
        kwargs = dict(
            attempt_id="attempt-001", profile=validate_endpoint_profile(profile()), requested_model="x",
            relay_reported_model="x", returned_model="x", independent_backend_identity=None,
            route="r", region="JP", egress="e", behavior_hash=HASH_A, input_hash=HASH_B,
            output_hash=HASH_C, latency_ms=1, operator_billing={"status":"UNKNOWN","value":None,"currency":None},
            upstream_billing={"status":"UNKNOWN","value":None,"currency":None},
        )
        with self.assertRaisesRegex(RelayContractError, "TOKEN_TOTAL"):
            build_relay_attempt_receipt(**kwargs, usage={"input_tokens":2,"output_tokens":3,"reasoning_tokens":2,"total_tokens":7})

    def test_profiled_router_keeps_business_dispatch_signature(self):
        catalog = EndpointProfileCatalog(
            profiles=[profile()], credential_bindings=[binding()],
            model_alias_bindings=[alias()], protocol_manifests=[manifest()],
        )
        fake_client = mock.Mock()
        fake_client.invoke.return_value = {"text": "OK", "provider": "ExampleRelay"}
        config = GatewayConfig((Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "models.yaml")
        router = ProfiledRouter(config, catalog, {"distill": "relay-openai-chat-v1"}, client=fake_client, clock=lambda: NOW)
        result = router.dispatch("distill", {"messages": [{"role":"user","content":"OK"}]})
        self.assertEqual(result["text"], "OK")
        fake_client.invoke.assert_called_once()

    def test_schema_assets_are_valid_json_and_no_secret_fields(self):
        schema_dir = (Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "model_api_relay" / "schemas"
        expected = {
            "model_api_endpoint_profile_v1.schema.json",
            "credential_endpoint_binding_v1.schema.json",
            "model_alias_binding_v1.schema.json",
            "protocol_adapter_manifest_v1.schema.json",
            "relay_attempt_evidence_v1.schema.json",
            "relay_compatibility_report_v1.schema.json",
        }
        self.assertEqual({p.name for p in schema_dir.glob("*.json")}, expected)
        for path in schema_dir.glob("*.json"):
            raw = path.read_text("utf-8")
            json.loads(raw)
            self.assertNotRegex(raw, r"(?i)sk-[a-z0-9]{12,}")

    def test_profile_bundle_loader_is_root_and_hash_bound(self):
        import tempfile
        from model_gateway.execution_core.contracts import bytes_sha256
        bundle = {
            "schema_version": "MODEL_API_RELAY_ENDPOINT_PROFILE_BUNDLE_V1",
            "profiles": [profile()],
            "credential_bindings": [binding()],
            "model_alias_bindings": [alias()],
            "protocol_manifests": [manifest()],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "profiles.json"
            raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode()
            path.write_bytes(raw)
            loaded = EndpointProfileCatalog.from_bundle(path, allowed_root=root, expected_sha256=bytes_sha256(raw))
            self.assertEqual(loaded.resolve("relay-openai-chat-v1", role="distill", capability="chat", at=NOW).profile["profile_id"], "relay-openai-chat-v1")
            with self.assertRaises(RelayContractError):
                EndpointProfileCatalog.from_bundle(path, allowed_root=root, expected_sha256=HASH_A)

    def test_safe_client_injects_one_exact_credential_and_returns_no_secret(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def getcode(self): return self.status
            def read(self, _limit):
                return json.dumps({"model":"deepseek-v4-flash","choices":[{"message":{"content":"OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}).encode()
        captured = {}
        class Opener:
            def open(self, request, timeout):
                captured["url"] = request.full_url
                captured["authorization"] = request.get_header("Authorization")
                captured["timeout"] = timeout
                return Response()
        catalog = EndpointProfileCatalog(profiles=[profile()], credential_bindings=[binding()], model_alias_bindings=[alias()], protocol_manifests=[manifest()])
        resolved = catalog.resolve("relay-openai-chat-v1", role="distill", capability="chat", at=NOW)
        client = SafeRelayClient(opener=Opener(), environ={"MODEL_API_RELAY_RELAY_KEY":"secret-value"}, resolver=lambda *_args, **_kwargs: [(2,1,6,"",("8.8.8.8",443))], monotonic=iter([1.0,1.01]).__next__)
        slot = GatewayConfig((Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "models.yaml").slot("distill")
        result = client.invoke(resolved, slot, {"messages":[{"role":"user","content":"Reply OK"}],"max_tokens":8})
        self.assertEqual(captured["authorization"], "Bearer secret-value")
        self.assertEqual(captured["url"], "https://relay.example.test/v1/chat/completions")
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(result["text"], "OK")

    def test_safe_client_blocks_missing_credential_private_dns_and_redirect(self):
        catalog = EndpointProfileCatalog(profiles=[profile()], credential_bindings=[binding()], model_alias_bindings=[alias()], protocol_manifests=[manifest()])
        resolved = catalog.resolve("relay-openai-chat-v1", role="distill", capability="chat", at=NOW)
        slot = GatewayConfig((Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "models.yaml").slot("distill")
        opener = mock.Mock()
        with self.assertRaisesRegex(RelayContractError, "CREDENTIAL_NOT_PRESENT"):
            SafeRelayClient(opener=opener, environ={}).invoke(resolved, slot, {"messages":[{"role":"user","content":"OK"}]})
        opener.assert_not_called()
        with self.assertRaisesRegex(RelayContractError, "DNS_PRIVATE_ADDRESS_FORBIDDEN"):
            SafeRelayClient(opener=opener, environ={"MODEL_API_RELAY_RELAY_KEY":"x"}, resolver=lambda *_a, **_k: [(2,1,6,"",("127.0.0.1",443))]).invoke(resolved, slot, {"messages":[{"role":"user","content":"OK"}]})

class QualificationAndAdversarialTests(unittest.TestCase):
    def resolved(self):
        return catalog_for().resolve(
            "relay-openai-chat-v1", role="distill", capability="chat", at=NOW
        )

    def test_qualification_key_is_deterministic_mutation_sensitive_and_secret_free(self):
        resolved = self.resolved()
        first = build_relay_qualification_key(
            logical_role="distill",
            execution_profile_revision="r1",
            resolved=resolved,
            platform="windows-amd64",
            runtime="python-3.13",
        )
        second = build_relay_qualification_key(
            logical_role="distill",
            execution_profile_revision="r1",
            resolved=resolved,
            platform="windows-amd64",
            runtime="python-3.13",
        )
        mutated = build_relay_qualification_key(
            logical_role="distill",
            execution_profile_revision="r1",
            resolved=resolved,
            platform="windows-amd64",
            runtime="python-3.14",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first["qualification_key"], mutated["qualification_key"])
        serialized = json.dumps(first)
        self.assertNotIn("secret", serialized.lower())
        self.assertNotIn("prompt", serialized.lower())

    def test_direct_relay_identity_collision_and_cross_binding_are_rejected(self):
        with self.assertRaisesRegex(RelayContractError, "CREDENTIAL_TRANSPORT_MISMATCH"):
            validate_credential_binding(binding(binding_kind="direct"), profile())
        direct = profile(
            transport_kind="DIRECT_PROVIDER",
            access_mode="DIRECT_API",
            endpoint_operator="DeepSeek",
            origin="https://api.deepseek.com",
            allowed_origins=["https://api.deepseek.com"],
            credential_binding_id="deepseek-direct-r1",
            credential_ref="DEEPSEEK_API_KEY",
        )
        direct_binding = binding(
            credential_binding_id="deepseek-direct-r1",
            credential_ref="DEEPSEEK_API_KEY",
            binding_kind="direct",
            endpoint_operator="DeepSeek",
            allowed_origin="https://api.deepseek.com",
        )
        direct_alias = alias(endpoint_operator="DeepSeek")
        with self.assertRaisesRegex(RelayContractError, "PROFILE_DUPLICATE"):
            EndpointProfileCatalog(
                profiles=[profile(), direct],
                credential_bindings=[binding(), direct_binding],
                model_alias_bindings=[alias(), direct_alias],
                protocol_manifests=[manifest()],
            )

    def test_unicode_wildcard_host_and_model_alias_drift_fail_closed(self):
        for origin in ("https://reláy.example", "https://*.example.test", "https://relay.example.test."):
            with self.subTest(origin=origin), self.assertRaises(RelayContractError):
                validate_endpoint_profile(profile(origin=origin, allowed_origins=[origin]))
        with self.assertRaisesRegex(RelayContractError, "PROFILE_ALIAS_BINDING_MISMATCH"):
            catalog_for(a=alias(request_model_alias="drifted-alias"))

    def test_identity_conflict_unverified_verified_and_generated_claim_ignored(self):
        unverified = classify_backend_identity(
            transport_kind="MODEL_API_RELAY",
            requested_model="model-a",
            relay_reported_model="model-a",
            generated_text_claim="I am model-b",
        )
        self.assertEqual(unverified["identity_grade"], "RELAY_REPORTED_UNVERIFIED")
        self.assertIsNone(unverified["returned_model"])
        conflict = classify_backend_identity(
            transport_kind="MODEL_API_RELAY",
            requested_model="model-a",
            relay_reported_model="model-a",
            response_header_models=["model-b"],
        )
        self.assertEqual(conflict["identity_grade"], "RELAY_REPORTED_CONFLICT")
        verified = classify_backend_identity(
            transport_kind="MODEL_API_RELAY",
            requested_model="model-a",
            relay_reported_model="model-a",
            independent_backend_identity="model-a",
        )
        self.assertEqual(verified["identity_grade"], "RELAY_BACKEND_VERIFIED")

    def test_capability_loss_and_qualification_axes_are_separate(self):
        preserved = build_capability_loss_map(
            source_protocol="OPENAI_RESPONSES",
            target_protocol="OPENAI_RESPONSES",
            required_capabilities=["system", "tools", "schema", "stream", "usage"],
            preserved_capabilities=["system", "tools", "schema", "stream", "usage"],
        )
        translated = build_capability_loss_map(
            source_protocol="GEMINI_GENERATE_CONTENT",
            target_protocol="OPENAI_CHAT_COMPLETIONS",
            required_capabilities=["system", "schema", "stream", "usage"],
            preserved_capabilities=["system", "stream"],
        )
        self.assertTrue(preserved["native_interface_replacement"])
        self.assertFalse(translated["native_interface_replacement"])
        self.assertEqual(translated["lost_capabilities"], ["schema", "usage"])
        transport = evaluate_qualification(
            q2_offline_pass=True, q4_transport_pass=True, role=None,
            identity_grade="RELAY_REPORTED_UNVERIFIED",
            identity_threshold="RELAY_BACKEND_VERIFIED",
            usage_status="UNKNOWN", billing_status="RECORDED_UNVERIFIED",
            region_egress_verified=True, role_contract_pass=False,
        )
        restricted = evaluate_qualification(
            q2_offline_pass=True, q4_transport_pass=True, role="analysis",
            identity_grade="RELAY_REPORTED_UNVERIFIED",
            identity_threshold="RELAY_BACKEND_VERIFIED",
            usage_status="ACTUAL_REPORTED", billing_status="ESTIMATED",
            region_egress_verified=True, role_contract_pass=True,
        )
        self.assertEqual(transport["verdict"], "TRANSPORT_COMPATIBLE")
        self.assertEqual(restricted["verdict"], "RESTRICTED_IDENTITY_UNVERIFIED")

    def test_compatibility_report_denominator_is_exact(self):
        report = build_compatibility_report(
            report_id="report-001",
            required_units=["unit-a", "unit-b"],
            entries=[
                {"unit": "unit-a", "verdict": "ROLE_QUALIFIED", "qualification_key": HASH_A},
                {"unit": "unit-b", "verdict": "BLOCKED", "qualification_key": HASH_B},
            ],
            evidence_refs=["sandbox:evidence/a"],
        )
        self.assertRegex(report["report_sha256"], r"^[0-9A-F]{64}$")
        with self.assertRaisesRegex(RelayContractError, "DENOMINATOR"):
            build_compatibility_report(
                report_id="bad", required_units=["unit-a", "unit-b"],
                entries=[{"unit": "unit-a", "verdict": "ROLE_QUALIFIED", "qualification_key": HASH_A}],
                evidence_refs=[],
            )

    def test_public_synthetic_ocr_contract_redacts_image_and_blocks_pdf_private(self):
        image = b"\x89PNG\r\n\x1a\nPUBLIC-SYNTHETIC"
        request, evidence = prepare_public_synthetic_ocr({
            "data_classification": "PUBLIC_SYNTHETIC",
            "mime_type": "image/png",
            "image_bytes": image,
            "page_number": 1,
            "source_sha256": hashlib.sha256(b"synthetic-source").hexdigest(),
            "prompt": "<image>\nFree OCR.",
            "max_tokens": 8,
        })
        self.assertTrue(request["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(request["messages"][0]["content"][1]["text"], "<image>\nFree OCR.")
        self.assertNotIn("image_bytes", json.dumps(evidence))
        self.assertEqual(evidence["payload_projection"], "IMAGE_AND_PROMPT_REDACTED")
        for changes in (
            {"data_classification": "PRIVATE"},
            {"mime_type": "application/pdf"},
            {"image_bytes": b""},
        ):
            payload = {
                "data_classification": "PUBLIC_SYNTHETIC", "mime_type": "image/png",
                "image_bytes": image, "page_number": 1,
                "source_sha256": HASH_A, "prompt": "OK",
            }
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(RelayContractError):
                prepare_public_synthetic_ocr(payload)


class ProtocolFailureMatrixTests(unittest.TestCase):
    def setUp(self):
        self.registry = RelayProtocolRegistry()

    def test_all_required_http_statuses_are_exact_and_retry_free(self):
        for status in (400, 401, 403, 404, 409, 429, 500, 502, 503, 504):
            with self.subTest(status=status), self.assertRaisesRegex(RelayContractError, f"HTTP_{status}"):
                self.registry.parse_response(
                    "OPENAI_CHAT_COMPLETIONS", profile(), status,
                    "application/json", b'{"error":{"message":"synthetic"}}',
                )

    def test_malformed_json_sse_duplicate_terminal_and_oversize_are_rejected(self):
        cases = [
            ("application/json", b"{"),
            ("text/event-stream", b"data: {bad}\n\n"),
            ("text/html", b"<html>not-json</html>"),
        ]
        for content_type, raw in cases:
            with self.subTest(content_type=content_type), self.assertRaises(RelayContractError):
                self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", profile(), 200, content_type, raw)
        duplicate = (
            'data: {"model":"deepseek-v4-flash","choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
            "data: [DONE]\n\ndata: [DONE]\n\n"
        ).encode()
        with self.assertRaisesRegex(RelayContractError, "DUPLICATE_TERMINAL"):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", profile(), 200, "text/event-stream", duplicate)
        with self.assertRaisesRegex(RelayContractError, "RESPONSE_OVERSIZE"):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", profile(), 200, "application/json", b"x" * (1024 * 1024 + 1))

    def test_missing_usage_is_unknown_not_zero_and_invalid_usage_is_rejected(self):
        chat = {"model": "deepseek-v4-flash", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        parsed = self.registry.parse_response(
            "OPENAI_CHAT_COMPLETIONS", profile(), 200, "application/json", json.dumps(chat).encode()
        )
        self.assertIsNone(parsed["usage"])
        self.assertEqual(parsed["usage_status"], "UNKNOWN")
        rerank_profile = profile(protocol_family="PROVIDER_RERANK", path_template="/v1/rerank", capabilities=["rerank"], rerank_score_range=[0.0, 1.0])
        rerank = {"model": "BAAI/bge-reranker-v2-m3", "results": [{"index": 0, "relevance_score": 0.5}]}
        parsed = self.registry.parse_response(
            "PROVIDER_RERANK", rerank_profile, 200, "application/json",
            json.dumps(rerank).encode(), expected_count=1,
        )
        self.assertIsNone(parsed["usage"])
        bad = {**chat, "usage": {"prompt_tokens": float("nan"), "completion_tokens": 1, "total_tokens": 1}}
        with self.assertRaisesRegex(RelayContractError, "USAGE_INVALID"):
            self.registry.parse_response(
                "OPENAI_CHAT_COMPLETIONS", profile(), 200, "application/json", json.dumps(bad).encode()
            )

    def test_payload_control_plane_injection_and_invalid_structured_output_are_blocked(self):
        for field in ("endpoint", "headers", "credential_ref", "model", "proxy"):
            payload = {"messages": [{"role": "user", "content": "OK"}], field: "attacker"}
            with self.subTest(field=field), self.assertRaisesRegex(RelayContractError, "CONTROL_PLANE"):
                self.registry.build_request("OPENAI_CHAT_COMPLETIONS", profile(), payload)
        schema = {
            "type": "object", "properties": {"status": {"type": "string", "enum": ["OK"]}},
            "required": ["status"], "additionalProperties": False,
        }
        good = {"model": "deepseek-v4-flash", "choices": [{"message": {"content": '{"status":"OK"}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", profile(), 200, "application/json", json.dumps(good).encode(), output_schema=schema)
        bad = copy.deepcopy(good)
        bad["choices"][0]["message"]["content"] = '{"status":"BAD"}'
        with self.assertRaisesRegex(RelayContractError, "STRUCTURED_OUTPUT"):
            self.registry.parse_response("OPENAI_CHAT_COMPLETIONS", profile(), 200, "application/json", json.dumps(bad).encode(), output_schema=schema)

    def test_gemini_safety_block_is_classified_not_transport_error(self):
        p = profile(protocol_family="GEMINI_GENERATE_CONTENT", path_template="/v1beta/models/{model}:generateContent", capabilities=["chat_structured"])
        body = {"modelVersion": "gemini-3-pro-preview", "promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 0, "totalTokenCount": 1}}
        parsed = self.registry.parse_response("GEMINI_GENERATE_CONTENT", p, 200, "application/json", json.dumps(body).encode())
        self.assertTrue(parsed["safety_blocked"])
        self.assertEqual(parsed["finish_reason"], "SAFETY_BLOCK:SAFETY")

    def test_embedding_dimension_and_rerank_score_contracts_block_semantic_drift(self):
        embedding_profile = profile(
            protocol_family="OPENAI_EMBEDDINGS", path_template="/v1/embeddings",
            capabilities=["embedding"], expected_embedding_dimension=3,
        )
        embedding = {
            "model": "BAAI/bge-m3", "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
        with self.assertRaisesRegex(RelayContractError, "DIMENSION_DRIFT"):
            self.registry.parse_response(
                "OPENAI_EMBEDDINGS", embedding_profile, 200, "application/json",
                json.dumps(embedding).encode(), expected_count=1,
            )
        rerank_profile = profile(
            protocol_family="PROVIDER_RERANK", path_template="/v1/rerank",
            capabilities=["rerank"], rerank_score_range=[0.0, 1.0],
        )
        rerank = {"model": "BAAI/bge-reranker-v2-m3", "results": [{"index": 0, "relevance_score": 1.1}]}
        with self.assertRaisesRegex(RelayContractError, "SCORE_OUT_OF_RANGE"):
            self.registry.parse_response(
                "PROVIDER_RERANK", rerank_profile, 200, "application/json",
                json.dumps(rerank).encode(), expected_count=1,
            )

    def test_direct_and_relay_request_shapes_differ_only_by_profile_control_plane(self):
        payload = {"messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 8, "stream": False}
        relay_profile = profile()
        direct_profile = profile(
            profile_id="direct-openai-chat-v1", transport_kind="DIRECT_PROVIDER",
            access_mode="DIRECT_API", endpoint_operator="DeepSeek",
            origin="https://api.deepseek.com", allowed_origins=["https://api.deepseek.com"],
            credential_binding_id="direct-deepseek-r1", credential_ref="DEEPSEEK_API_KEY",
            route_id="direct-openai-chat-v1",
        )
        relay_request = self.registry.build_request("OPENAI_CHAT_COMPLETIONS", relay_profile, payload)
        direct_request = self.registry.build_request("OPENAI_CHAT_COMPLETIONS", direct_profile, payload)
        self.assertEqual(relay_request.body, direct_request.body)
        self.assertEqual(relay_request.path, direct_request.path)
        self.assertNotEqual(relay_profile["origin"], direct_profile["origin"])

    def test_anthropic_parameters_responses_tools_and_gemini_system_are_preserved(self):
        anthropic_profile = profile(protocol_family="ANTHROPIC_MESSAGES", path_template="/v1/messages")
        anthropic = self.registry.build_request(
            "ANTHROPIC_MESSAGES", anthropic_profile,
            {
                "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "OK"}],
                "thinking": {"type": "adaptive"}, "output_config": {"effort": "low"},
                "cache_control": {"type": "ephemeral"}, "max_tokens": 8,
                "anthropic_beta": "structured-outputs-2025-11-13",
            },
        )
        self.assertEqual(anthropic.body["thinking"]["type"], "adaptive")
        self.assertEqual(anthropic.body["output_config"]["effort"], "low")
        self.assertEqual(anthropic.body["cache_control"]["type"], "ephemeral")
        self.assertIn("anthropic-beta", anthropic.headers)

        responses_profile = profile(
            protocol_family="OPENAI_RESPONSES", path_template="/v1/responses",
            capabilities=["chat_structured_tools"],
        )
        tool = {"type": "function", "name": "synthetic_lookup", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False}}
        responses = self.registry.build_request(
            "OPENAI_RESPONSES", responses_profile,
            {"input": "Return OK", "tools": [tool], "max_output_tokens": 8},
        )
        self.assertEqual(responses.body["tools"], [tool])

        gemini_profile = profile(
            protocol_family="GEMINI_GENERATE_CONTENT",
            path_template="/v1beta/models/{model}:generateContent",
            capabilities=["chat_structured"],
        )
        gemini = self.registry.build_request(
            "GEMINI_GENERATE_CONTENT", gemini_profile,
            {
                "contents": [{"role": "user", "parts": [{"text": "OK"}]}],
                "system_instruction": {"parts": [{"text": "system"}]},
                "response_schema": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"], "additionalProperties": False},
                "max_output_tokens": 8,
            },
        )
        self.assertIn("systemInstruction", gemini.body)
        self.assertEqual(gemini.body["generationConfig"]["responseMimeType"], "application/json")


class RoutingEvidenceAndTransportTests(unittest.TestCase):
    def resolved(self):
        return catalog_for().resolve(
            "relay-openai-chat-v1", role="distill", capability="chat", at=NOW
        )

    def test_four_route_policies_relay_only_and_local_exempt_are_deterministic(self):
        planner = RelayRoutePlanner({
            "distill": {
                "direct": "direct-profile", "relay": "relay-profile",
                "ordered_candidates": ["relay-profile", "direct-profile"],
            }
        })
        qualified = {"direct-profile": True, "relay-profile": True}
        self.assertEqual(planner.decide("distill", policy="direct_only", qualified_profiles=qualified)["selected_profile"], "direct-profile")
        relay = planner.decide("distill", policy="relay_only", qualified_profiles=qualified)
        self.assertEqual(relay["selected_profile"], "relay-profile")
        self.assertEqual(relay["external_attempts"], 0)
        ordered = planner.decide("distill", policy="explicit_ordered_candidates", qualified_profiles=qualified)
        self.assertEqual(ordered["selected_profile"], "relay-profile")
        self.assertEqual(planner.decide("distill", policy="blocked", qualified_profiles=qualified)["status"], "BLOCKED")
        local = planner.decide("distill", policy="relay_only", qualified_profiles=qualified, local_exempt=True)
        self.assertEqual(local["external_attempts"], 0)
        self.assertEqual(local["status"], "BLOCKED_LOCAL_EXEMPT")
        with self.assertRaisesRegex(RelayContractError, "ROLLBACK_TARGET_NOT_QUALIFIED"):
            planner.rollback("distill", qualified_profiles={"direct-profile": False})

    def test_attempt_ledger_preserves_blocked_failure_lineage_and_ceiling(self):
        p = self.resolved().profile
        blocked = build_presend_blocked_receipt(
            attempt_id="blocked-001", profile=p, requested_model="deepseek-v4-flash",
            route="relay-openai-chat-v1", region="JP", egress="egress-profile-a",
            behavior_hash=HASH_A, input_hash=HASH_B, reason_code="CREDENTIAL_NOT_PRESENT",
        )
        passed = build_relay_attempt_receipt(
            attempt_id="attempt-001", profile=p, requested_model="deepseek-v4-flash",
            relay_reported_model="deepseek-v4-flash", returned_model="deepseek-v4-flash",
            independent_backend_identity="deepseek-v4-flash", route="relay-openai-chat-v1",
            region="JP", egress="egress-profile-a", behavior_hash=HASH_A,
            input_hash=HASH_B, output_hash=HASH_C, latency_ms=1.0,
            usage={"input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0, "total_tokens": 2},
            operator_billing={"status": "ESTIMATED", "value": 0.00001, "currency": "USD"},
            upstream_billing={"status": "RECORDED_UNVERIFIED", "value": None, "currency": None},
            retry_of="blocked-001",
        )
        ledger = AttemptLedger(attempt_ceiling=1)
        ledger.append(blocked)
        ledger.append(passed)
        self.assertEqual(ledger.summary()["attempt_denominator"], 2)
        self.assertEqual(ledger.summary()["physical_request_starts"], 1)
        with self.assertRaisesRegex(RelayContractError, "DUPLICATE"):
            ledger.append(passed)
        extra = {**passed, "attempt_id": "attempt-002", "retry_of": "attempt-001"}
        with self.assertRaisesRegex(RelayContractError, "CEILING_EXHAUSTED"):
            ledger.append(extra)

    def test_client_classifies_redirect_timeout_disconnect_and_competing_env(self):
        resolved = self.resolved()
        slot = GatewayConfig((Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "models.yaml").slot("distill")
        resolver = lambda *_a, **_k: [(2, 1, 6, "", ("8.8.8.8", 443))]

        class RedirectOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 302, "Found", {"Location": "https://other.test"}, None)

        with self.assertRaisesRegex(RelayContractError, "REDIRECT_FORBIDDEN"):
            SafeRelayClient(opener=RedirectOpener(), environ={"MODEL_API_RELAY_RELAY_KEY": "dummy"}, resolver=resolver).invoke(resolved, slot, {"messages": [{"role": "user", "content": "OK"}]})

        for reason, code in ((socket.timeout(), "TRANSPORT_TIMEOUT"), (ConnectionResetError(), "TRANSPORT_DISCONNECT")):
            class FailedOpener:
                def open(self, request, timeout, _reason=reason):
                    raise urllib.error.URLError(_reason)
            with self.subTest(code=code), self.assertRaisesRegex(RelayContractError, code):
                SafeRelayClient(opener=FailedOpener(), environ={"MODEL_API_RELAY_RELAY_KEY": "dummy"}, resolver=resolver).invoke(resolved, slot, {"messages": [{"role": "user", "content": "OK"}]})

        opener = mock.Mock()
        with self.assertRaisesRegex(RelayContractError, "COMPETING_CREDENTIAL_BINDING"):
            SafeRelayClient(
                opener=opener,
                environ={"MODEL_API_RELAY_RELAY_KEY": "dummy", "DEEPSEEK_API_KEY": "present"},
                resolver=resolver,
                competing_credential_refs=("DEEPSEEK_API_KEY",),
            ).invoke(resolved, slot, {"messages": [{"role": "user", "content": "OK"}]})
        opener.assert_not_called()

    def test_real_loopback_fake_relay_captures_exact_shape_without_external_network(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                captured["path"] = self.path
                captured["authorization"] = self.headers.get("Authorization")
                captured["body"] = json.loads(self.rfile.read(size))
                raw = json.dumps({
                    "id": "synthetic-request-id", "model": "deepseek-v4-flash",
                    "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("x-request-id", "fake-relay-request-001")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            p = profile(
                profile_id="selfhosted-openai-chat-v1",
                transport_kind="SELF_HOSTED_LOOPBACK_RELAY",
                endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY",
                scheme="http", origin=origin, allowed_origins=[origin],
                credential_binding_id="selfhosted-dummy-r1",
                credential_ref="MODEL_API_RELAY_DUMMY_RELAY_CLIENT_TOKEN",
                route_id="selfhosted-openai-chat-v1",
            )
            b = binding(
                credential_binding_id="selfhosted-dummy-r1",
                credential_ref="MODEL_API_RELAY_DUMMY_RELAY_CLIENT_TOKEN",
                binding_kind="self_hosted_relay_client",
                endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY",
                allowed_origin=origin,
            )
            a = alias(endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY")
            resolved = catalog_for(p=p, b=b, a=a).resolve(
                "selfhosted-openai-chat-v1", role="distill", capability="chat", at=NOW
            )
            slot = GatewayConfig((Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "models.yaml").slot("distill")
            result = SafeRelayClient(
                environ={"MODEL_API_RELAY_DUMMY_RELAY_CLIENT_TOKEN": "dummy-only"}, timeout_seconds=5
            ).invoke(resolved, slot, {"messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 8})
            self.assertEqual(result["text"], "OK")
            self.assertEqual(result["request_id"], "fake-relay-request-001")
            self.assertEqual(captured["path"], "/v1/chat/completions")
            self.assertEqual(captured["authorization"], "Bearer dummy-only")
            self.assertNotIn("dummy-only", json.dumps(result))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_relay_ocr_reuses_existing_grounding_normalization(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "id": "synthetic-ocr-request",
                    "model": "deepseek-ai/DeepSeek-OCR",
                    "choices": [{
                        "message": {"content": "<|ref|>title<|/ref|><|det|>[[1,2,3,4]]<|/det|>\nOK"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode()

        class Opener:
            def open(self, _request, timeout):
                self.timeout = timeout
                return Response()

        origin = "http://127.0.0.1:54321"
        p = profile(
            profile_id="selfhosted-ocr-v1",
            transport_kind="SELF_HOSTED_LOOPBACK_RELAY",
            endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY",
            scheme="http", origin=origin, allowed_origins=[origin],
            credential_binding_id="selfhosted-dummy-r1",
            credential_ref="MODEL_API_RELAY_DUMMY_RELAY_CLIENT_TOKEN",
            route_id="selfhosted-ocr-v1",
            capabilities=["vision_ocr"], allowed_roles=["ocr"],
            logical_model_id="ocr-low-cost",
            request_model_alias="deepseek-ai/DeepSeek-OCR",
        )
        b = binding(
            credential_binding_id="selfhosted-dummy-r1",
            credential_ref="MODEL_API_RELAY_DUMMY_RELAY_CLIENT_TOKEN",
            binding_kind="self_hosted_relay_client",
            endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY",
            allowed_origin=origin,
        )
        a = alias(
            endpoint_operator="MODEL_API_RELAY_SELF_HOSTED_LOOPBACK_RELAY",
            logical_model_id="ocr-low-cost",
            request_model_alias="deepseek-ai/DeepSeek-OCR",
        )
        m = manifest(capabilities=["vision_ocr"])
        resolved = catalog_for(p=p, b=b, a=a, m=m).resolve(
            "selfhosted-ocr-v1", role="ocr", capability="vision_ocr", at=NOW
        )
        slot = GatewayConfig((Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "models.yaml").slot("ocr")
        result = SafeRelayClient(
            opener=Opener(), environ={"MODEL_API_RELAY_DUMMY_RELAY_CLIENT_TOKEN": "dummy-only"}
        ).invoke(resolved, slot, {"messages": [{"role": "user", "content": "synthetic"}]}, capability="vision_ocr")
        self.assertEqual(result["text"], "OK")

    def test_all_schema_assets_validate_and_examples_conform(self):
        schema_dir = (Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "model_api_relay" / "schemas"
        schemas = {path.name: json.loads(path.read_text("utf-8")) for path in schema_dir.glob("*.json")}
        for schema in schemas.values():
            jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(profile(), schemas["model_api_endpoint_profile_v1.schema.json"])
        jsonschema.validate(binding(), schemas["credential_endpoint_binding_v1.schema.json"])
        jsonschema.validate(alias(), schemas["model_alias_binding_v1.schema.json"])
        jsonschema.validate(manifest(), schemas["protocol_adapter_manifest_v1.schema.json"])


if __name__ == "__main__":
    unittest.main()
