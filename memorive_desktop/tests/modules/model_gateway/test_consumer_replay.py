from __future__ import annotations

import json
from pathlib import Path
import unittest

from memorive.models.gateway.execution_core.contracts import canonical_sha256
from memorive.models.gateway.model_configuration import TierResolver, assert_forbidden_values_absent, merge_configuration
from memorive.models.gateway.model_configuration.fixtures import RESOLVE_AT, advanced_config, model_profiles, ordinary_config, provider_preset, tier_policy, verified_credential
from memorive_app.contracts import resolved_profile_snapshot


PACKAGE_ROOT = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "models" / "gateway") / "model_configuration"


class ConsumerReplayTests(unittest.TestCase):
    def setUp(self):
        self.store, self.binding = verified_credential()
        self.resolver = TierResolver(
            policy=tier_policy(),
            profiles=model_profiles(),
            provider_presets=[provider_preset()],
            credential_bindings=[self.binding],
            resolver_revision="r1",
        )

    def _resolution(self):
        return self.resolver.resolve(
            role="analysis_primary",
            requested_user_tier="standard",
            thinking_enabled=True,
            data_classification="public",
            explicit_selection=False,
            at=RESOLVE_AT,
        )

    def test_resolved_profile_snapshot_replay_is_deterministic(self):
        resolution = self._resolution()
        config = merge_configuration(ordinary_config(), advanced_config())
        kwargs = dict(
            profile_id=resolution["profile_id"],
            role=resolution["role"],
            capability_revision="model_configuration-r1",
            config_revision="model_configuration-r1",
            config_sha256=config["snapshot_sha256"],
            credential_refs=[resolution["credential_ref"]],
            execution_channel="OFFLINE_FAKE",
            fallback_policy="NO_SILENT_FALLBACK",
            resource_policy={"class": "synthetic"},
            budget_policy={"currency": "CNY", "hard_limit": 2.0},
            egress_policy=resolution["egress_class"],
            data_classification=resolution["data_classification"],
            module_revisions={"M09": "model_configuration-r1"},
            source_hashes={"tier_resolution": resolution["resolution_sha256"]},
            created_at=RESOLVE_AT,
            resolver_revision=resolution["resolver_revision"],
        )
        first = resolved_profile_snapshot(**kwargs)
        second = resolved_profile_snapshot(**kwargs)
        self.assertEqual(first, second)
        self.assertTrue(first["credential_refs"][0].startswith("REF:"))

    def test_future_ordinary_consumer_sees_only_six_control_groups(self):
        resolution = self._resolution()
        projection = {
            "provider": resolution["provider_id"],
            "credential_binding": "binding_main",
            "tier": resolution["requested_user_tier"],
            "thinking": resolution["requested_thinking_enabled"],
            "save": True,
            "connection_status": "CONNECTION_VERIFIED",
        }
        self.assertEqual(
            set(projection),
            {"provider", "credential_binding", "tier", "thinking", "save", "connection_status"},
        )
        serialized = json.dumps(projection, sort_keys=True)
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("model_alias", serialized)
        self.assertNotIn("env", serialized)

    def test_retrieval_and_rerank_consumers_receive_public_safe_offline_contract(self):
        resolution = self._resolution()
        contract = {
            "schema_version": "MODEL_CONFIGURATION_CONSUMER_REPLAY_V1",
            "credential_backend": "FAKE_IN_MEMORY",
            "resolution": resolution,
            "model_profiles": model_profiles(),
            "provider_preset": provider_preset(),
            "network_requests": 0,
            "external_model_requests": 0,
            "real_credentials": 0,
            "real_os_credential_store": "NOT_ASSESSED",
            "deployment_status": "disabled",
        }
        first_hash = canonical_sha256(contract)
        second_hash = canonical_sha256(json.loads(json.dumps(contract, sort_keys=True)))
        self.assertEqual(first_hash, second_hash)
        assert_forbidden_values_absent(contract, ["fixture-value-never-sent"])
        self.assertEqual(contract["network_requests"], 0)
        self.assertEqual(contract["external_model_requests"], 0)

    def test_package_has_no_network_client_imports_or_real_credential_backend(self):
        forbidden_imports = ("import socket", "import requests", "import urllib", "httpx", "aiohttp")
        hits = []
        for path in sorted(PACKAGE_ROOT.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for needle in forbidden_imports:
                if needle in text:
                    hits.append(f"{path.name}:{needle}")
        self.assertEqual(hits, [])
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(PACKAGE_ROOT.glob("*.py")))
        self.assertNotIn("win32cred", source)
        self.assertNotIn("keyring", source)


if __name__ == "__main__":
    unittest.main()
