from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_gateway.config import SlotConfig
from model_gateway.errors import GatewayError
from model_gateway.gateway import ModelGateway
from model_gateway.region_gate import _normalized_https_url, _parse_region, _proxy_route_id, _route_context, preflight_for_slot, validate_dispatch_receipt
from model_gateway.region_gate import _route_binding
TRUSTED_GEO_URL = "https://www.cloudflare.com/cdn-cgi/trace"


def _overseas_slot() -> SlotConfig:
    return SlotConfig(task_type="analysis", enabled=True, provider="openai", model_id="x", endpoint="https://api.example/v1")


def _evidence(slot, code="JP", **overrides):
    context = _route_context(slot)
    value = {
        "country_code": code,
        "geo_provider": context["geo_provider"],
        "geo_source_id": context["geo_source_id"],
        "transport": "https",
        "egress_route_id": context["egress_route_id"],
        "route_binding": _route_binding(slot),
    }
    value.update(overrides)
    return value


@patch.dict(os.environ, {"MEMORIVE_MODEL_GATEWAY_GEO_URL": TRUSTED_GEO_URL})
class RegionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.route_patcher = patch("model_gateway.region_gate._proxy_route_id", return_value="A" * 64)
        self.route_patcher.start()
        self.addCleanup(self.route_patcher.stop)

    def test_alias_and_malformed_country_codes_fail_closed(self):
        self.assertEqual(_parse_region('{"country":"China"}'), "CN")
        self.assertIsNone(_parse_region('{"country":"CHINA-LONG"}'))
        slot = _overseas_slot()
        result = preflight_for_slot(slot, probe=lambda _slot: _evidence(slot, "CHINA"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["gated"])

    def test_unknown_insecure_and_route_mismatch_fail_closed(self):
        slot = _overseas_slot()
        cases = [
            {"country_code": None, "transport": "https", "route_binding": _route_binding(slot)},
            _evidence(slot, transport="http"),
            _evidence(slot, route_binding="0" * 64),
        ]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                result = preflight_for_slot(slot, probe=lambda _slot, e=evidence: e)
                self.assertFalse(result["ok"])
                self.assertTrue(result["gated"])

    def test_cn_blocks_and_route_bound_https_non_cn_passes(self):
        slot = _overseas_slot()
        blocked = preflight_for_slot(slot, probe=lambda _slot: _evidence(slot, "CN"))
        allowed = preflight_for_slot(slot, probe=lambda _slot: _evidence(slot, "JP"))
        self.assertTrue(blocked["gated"])
        self.assertFalse(blocked["ok"])
        self.assertTrue(allowed["ok"])
        self.assertFalse(allowed["gated"])
        self.assertTrue(allowed["evidence_valid"])

    def test_arbitrary_https_geo_source_fails_closed(self):
        slot = _overseas_slot()
        with patch.dict(os.environ, {"MEMORIVE_MODEL_GATEWAY_GEO_URL": "https://untrusted.example/geo"}):
            result = preflight_for_slot(
                slot,
                probe=lambda _slot: {
                    "country_code": "JP",
                    "geo_provider": "untrusted.example",
                    "geo_source_id": "0" * 64,
                    "transport": "https",
                    "egress_route_id": "1" * 64,
                    "route_binding": "2" * 64,
                },
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["gated"])
        self.assertFalse(result["evidence_valid"])

    def test_split_route_and_post_probe_route_change_fail_closed(self):
        slot = _overseas_slot()
        same_route = "A" * 64
        with patch("model_gateway.region_gate._proxy_route_id", return_value=same_route):
            receipt = preflight_for_slot(slot, probe=lambda _slot: _evidence(slot, "JP"))
            self.assertTrue(receipt["ok"])
            self.assertTrue(validate_dispatch_receipt(slot, receipt))

        def split_route(url):
            return "B" * 64 if "cloudflare.com" in url else "C" * 64

        with patch("model_gateway.region_gate._proxy_route_id", side_effect=split_route):
            split = preflight_for_slot(slot, probe=lambda _slot: _evidence(slot, "JP"))
            self.assertFalse(split["ok"])
            self.assertTrue(split["gated"])
            self.assertFalse(validate_dispatch_receipt(slot, receipt))

    def test_gateway_requires_ok_and_not_gated_before_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "models.yaml"
            config.write_text(
                "schema_version: '1'\nslots:\n  analysis:\n    enabled: true\n    provider: openai\n    model_id: x\n    endpoint: https://api.example/v1\n",
                encoding="utf-8",
            )
            gateway = ModelGateway(config, root, region_probe=lambda slot: _evidence(slot, "JP", transport="http"))
            calls = []
            gateway.router.dispatch = lambda *args, **kwargs: calls.append((args, kwargs)) or {"text": "unexpected"}
            gateway.cache.get = lambda _key: None
            with self.assertRaises(GatewayError):
                gateway.call("analysis", {"input": "public-safe"})
            self.assertEqual(calls, [])

    def test_all_remote_dispatch_entrypoints_require_region_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "models.yaml"
            config.write_text(
                "schema_version: '1'\nslots:\n"
                "  embedding:\n    enabled: true\n    provider: openai\n    model_id: x\n    endpoint: https://api.example/v1\n"
                "  reranking:\n    enabled: true\n    provider: openai\n    model_id: x\n    endpoint: https://api.example/v1\n"
                "  ocr:\n    enabled: true\n    provider: openai\n    model_id: x\n    endpoint: https://api.example/v1\n    prompt_version: 1\n",
                encoding="utf-8",
            )
            prompt_dir = root / "ocr"
            prompt_dir.mkdir()
            (prompt_dir / "v1.md").write_text("extract text\n", encoding="utf-8")
            gateway = ModelGateway(
                config,
                root,
                region_probe=lambda slot: _evidence(slot, "JP", transport="http"),
            )
            calls = []
            gateway.router.dispatch = lambda *args, **kwargs: calls.append((args, kwargs)) or {}
            operations = [
                lambda: gateway.embed("embedding", ["public-safe"]),
                lambda: gateway.rerank("reranking", "q", ["public-safe"]),
                lambda: gateway.ocr(
                    "ocr",
                    b"image",
                    mime_type="image/png",
                    page_number=1,
                    source_sha256="0" * 64,
                    data_ownership="self",
                ),
            ]
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(GatewayError):
                        operation()
            self.assertEqual(calls, [])


class MalformedRouteInputTests(unittest.TestCase):
    def test_malformed_trusted_geo_port_returns_untrusted_and_preflight_fails_closed(self):
        malformed = "https://www.cloudflare.com:bad/cdn-cgi/trace"
        self.assertIsNone(_normalized_https_url(malformed))
        with patch.dict(os.environ, {"MEMORIVE_MODEL_GATEWAY_GEO_URL": malformed}):
            result = preflight_for_slot(_overseas_slot(), probe=lambda _slot: {"country_code": "JP"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["gated"])
        self.assertFalse(result["evidence_valid"])

    def test_malformed_proxy_port_returns_untrusted_and_preflight_fails_closed(self):
        slot = _overseas_slot()
        malformed_proxy = "http://proxy.invalid:bad"
        with (
            patch("model_gateway.region_gate.urllib.request.proxy_bypass", return_value=False),
            patch("model_gateway.region_gate.urllib.request.getproxies", return_value={"https": malformed_proxy}),
        ):
            self.assertIsNone(_proxy_route_id(slot.endpoint))
            result = preflight_for_slot(slot, probe=lambda _slot: {"country_code": "JP"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["gated"])
        self.assertFalse(result["evidence_valid"])


if __name__ == "__main__":
    unittest.main()
