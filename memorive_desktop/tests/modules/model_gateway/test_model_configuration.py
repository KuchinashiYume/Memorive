from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from model_gateway.model_configuration import ERROR_CODE_CATALOG, FakeProvider, ModelConfigurationError, TierResolver, assert_forbidden_values_absent, check_connection, configuration_error_envelope, connection_not_run_receipt, legacy_env_name_reference, merge_configuration, redact_public, validate_advanced_config, validate_model_profile, validate_provider_preset, validate_tier_policy
from model_gateway.model_configuration.fixtures import EXPIRES_AT, OBSERVED_AT, RESOLVE_AT, advanced_config, disclosed_collapse_policy, model_profiles, ordinary_config, provider_preset, tier_policy, unavailable_policy, verified_credential
SCHEMA_ROOT = (Path(__file__).resolve().parents[3] / "source" / "model_gateway") / "model_configuration" / "schemas"


def error_code(callable_):
    with unittest.TestCase().assertRaises(ModelConfigurationError) as caught:
        callable_()
    return caught.exception.code


def resolver(*, policy=None, profiles=None, preset=None, binding=None):
    if binding is None:
        _, binding = verified_credential()
    return TierResolver(
        policy=policy or tier_policy(),
        profiles=profiles or model_profiles(),
        provider_presets=[preset or provider_preset()],
        credential_bindings=[binding],
        resolver_revision="r1",
    )


class CredentialAndConnectionTests(unittest.TestCase):
    def test_fake_store_never_returns_secret_and_preserves_state_axes(self):
        store, binding = verified_credential()
        serialized = json.dumps(binding, sort_keys=True)
        self.assertNotIn("fixture-value-never-sent", serialized)
        self.assertTrue(store.has_secret(binding["credential_ref"]))
        receipt = check_connection(
            store=store,
            credential_ref=binding["credential_ref"],
            provider_preset=provider_preset(),
            provider=FakeProvider("provider_fake"),
            requested_model_alias="model-fast",
            check_id="check-001",
            checked_at=RESOLVE_AT,
        )
        self.assertEqual(receipt["key_state"], "KEY_SAVED")
        self.assertEqual(receipt["connection_state"], "CONNECTION_VERIFIED")
        self.assertEqual(receipt["qualification_state"], "NOT_ASSESSED")
        self.assertEqual(receipt["enabled_state"], "DISABLED")
        self.assertEqual(receipt["network_requests"], 0)
        self.assertEqual(receipt["external_model_requests"], 0)
        self.assertNotIn("fixture-value-never-sent", json.dumps(receipt, sort_keys=True))

    def test_replace_and_revoke_create_new_ref_without_reusing_old_ref(self):
        store, original = verified_credential()
        replacement = store.replace(
            original["credential_ref"],
            secret_value="replacement-fixture-never-sent",
            rotated_at="2026-08-22T01:00:00+00:00",
        )
        self.assertNotEqual(original["credential_ref"], replacement["credential_ref"])
        self.assertEqual(store.metadata(original["credential_ref"])["state"], "REPLACED")
        self.assertFalse(store.has_secret(original["credential_ref"]))
        revoked = store.revoke(replacement["credential_ref"], revoked_at="2026-08-22T02:00:00+00:00")
        self.assertEqual(revoked["state"], "REVOKED")
        self.assertFalse(store.has_secret(replacement["credential_ref"]))
        self.assertEqual(
            error_code(
                lambda: check_connection(
                    store=store,
                    credential_ref=replacement["credential_ref"],
                    provider_preset=provider_preset(),
                    provider=FakeProvider("provider_fake"),
                    requested_model_alias="model-fast",
                    check_id="check-revoked",
                    checked_at=RESOLVE_AT,
                )
            ),
            "CREDENTIAL_REVOKED",
        )

    def test_second_save_requires_explicit_replace(self):
        store, _ = verified_credential()
        self.assertEqual(
            error_code(
                lambda: store.save(
                    binding_id="binding_main",
                    provider_id="provider_fake",
                    secret_value="second-fixture-never-sent",
                    account_label="Synthetic account",
                    created_at=RESOLVE_AT,
                )
            ),
            "CREDENTIAL_REPLACE_REQUIRED",
        )

    def test_verification_not_run_is_a_separate_offline_axis(self):
        _, binding = verified_credential()
        receipt = connection_not_run_receipt(
            credential_ref=binding["credential_ref"],
            provider_id="provider_fake",
            requested_model_alias="model-fast",
            check_id="check-not-run",
            recorded_at=RESOLVE_AT,
        )
        self.assertEqual(receipt["key_state"], "KEY_SAVED")
        self.assertEqual(receipt["connection_state"], "VERIFICATION_NOT_RUN")
        self.assertEqual(receipt["qualification_state"], "NOT_ASSESSED")
        self.assertEqual(receipt["enabled_state"], "DISABLED")
        self.assertEqual(receipt["network_requests"], 0)

    def test_connection_failures_do_not_set_quality_or_enablement(self):
        for scenario, expected, retryable in (
            ("auth_failure", "AUTHENTICATION_FAILED", False),
            ("timeout", "CONNECTION_TIMEOUT", True),
            ("malformed", "MALFORMED_PROVIDER_RESPONSE", False),
            ("identity_drift", "RETURNED_MODEL_IDENTITY_DRIFT", False),
        ):
            with self.subTest(scenario=scenario):
                store, binding = verified_credential()
                receipt = check_connection(
                    store=store,
                    credential_ref=binding["credential_ref"],
                    provider_preset=provider_preset(),
                    provider=FakeProvider("provider_fake", scenario=scenario),
                    requested_model_alias="model-fast",
                    check_id=f"check-{scenario}",
                    checked_at=RESOLVE_AT,
                )
                self.assertEqual(receipt["reason_code"], expected)
                self.assertEqual(receipt["connection_state"], "CONNECTION_FAILED")
                self.assertEqual(receipt["qualification_state"], "NOT_ASSESSED")
                self.assertEqual(receipt["enabled_state"], "DISABLED")
                self.assertEqual(receipt["retryable"], retryable)


class ContractTests(unittest.TestCase):
    def test_provider_preset_is_disabled_fake_and_pinned(self):
        accepted = validate_provider_preset(provider_preset())
        self.assertFalse(accepted["default_enabled"])
        self.assertEqual(accepted["connection_check_method"], "IN_PROCESS_FAKE")
        for mutation, code in (
            ({"default_enabled": True}, "PROVIDER_DEFAULT_ENABLE_FORBIDDEN"),
            ({"credential_backend_type": "WINDOWS_CREDENTIAL_MANAGER"}, "REAL_CREDENTIAL_BACKEND_NOT_AUTHORIZED"),
            ({"model_discovery_policy": "REMOTE_AUTO_DISCOVERY"}, "UNTRUSTED_MODEL_DISCOVERY_FORBIDDEN"),
        ):
            bad = {**provider_preset(), **mutation}
            self.assertEqual(error_code(lambda bad=bad: validate_provider_preset(bad)), code)

    def test_native_effort_exact_set_and_per_model_order_are_not_inferred(self):
        profile = validate_model_profile(model_profiles()[3])
        self.assertEqual(profile["native_effort_order"], ["high", "xhigh", "max"])
        bad = deepcopy(profile)
        bad["native_effort_order"] = ["high", "xhigh", "max", "ultra"]
        self.assertEqual(error_code(lambda: validate_model_profile(bad)), "NATIVE_EFFORT_ORDER_MISMATCH")

    def test_four_tiers_have_distinct_exact_profiles_and_maximum_uses_profile_highest(self):
        accepted = validate_tier_policy(tier_policy(), model_profiles())
        tiers = accepted["roles"]["analysis_primary"]
        self.assertEqual(tuple(tiers), ("economy", "standard", "high_quality", "maximum"))
        self.assertEqual(len({entry["profile_id"] for entry in tiers.values()}), 4)
        self.assertEqual(tiers["maximum"]["thinking_on_native_effort"], "max")
        bad = tier_policy()
        bad["roles"]["analysis_primary"]["maximum"]["thinking_on_native_effort"] = "xhigh"
        self.assertEqual(error_code(lambda: validate_tier_policy(bad, model_profiles())), "MAXIMUM_NOT_PROFILE_HIGHEST_EFFORT")

    def test_same_profile_on_adjacent_tiers_requires_disclosed_collapse(self):
        bad = tier_policy()
        bad["roles"]["analysis_primary"]["high_quality"] = deepcopy(
            bad["roles"]["analysis_primary"]["standard"]
        )
        bad["roles"]["analysis_primary"]["high_quality"]["requires_explicit_selection"] = False
        self.assertEqual(error_code(lambda: validate_tier_policy(bad, model_profiles())), "TIER_COLLAPSE_NOT_DECLARED")
        accepted = validate_tier_policy(disclosed_collapse_policy(), model_profiles())
        self.assertEqual(
            accepted["roles"]["analysis_primary"]["high_quality"]["resolution_status"],
            "COLLAPSED_DISCLOSED",
        )

    def test_unavailable_is_explicit_and_does_not_fabricate_profile(self):
        accepted = validate_tier_policy(unavailable_policy(), model_profiles())
        self.assertIsNone(accepted["roles"]["analysis_primary"]["high_quality"]["profile_id"])
        self.assertEqual(
            error_code(
                lambda: resolver(policy=unavailable_policy()).resolve(
                    role="analysis_primary",
                    requested_user_tier="high_quality",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "TIER_PROFILE_UNAVAILABLE",
        )

    def test_silent_fallback_is_rejected(self):
        bad = tier_policy()
        entry = bad["roles"]["analysis_primary"]["standard"]
        entry["fallback_profile_ids"] = ["analysis_economy_v1"]
        entry["fallback_requires_user_confirmation"] = False
        self.assertEqual(error_code(lambda: validate_tier_policy(bad, model_profiles())), "FALLBACK_REQUIRES_EXPLICIT_POLICY")


class ResolverTests(unittest.TestCase):
    def test_all_four_tiers_resolve_to_exact_profile_and_native_effort(self):
        r = resolver()
        expected = {
            "economy": ("analysis_economy_v1", "low"),
            "standard": ("analysis_standard_v1", "medium"),
            "high_quality": ("analysis_high_quality_v1", "high"),
            "maximum": ("analysis_maximum_v1", "max"),
        }
        for tier, pair in expected.items():
            with self.subTest(tier=tier):
                result = r.resolve(
                    role="analysis_primary",
                    requested_user_tier=tier,
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=tier == "maximum",
                    at=RESOLVE_AT,
                )
                self.assertEqual((result["profile_id"], result["native_effort"]), pair)
                self.assertFalse(result["fallback_used"])

    def test_maximum_requires_explicit_selection(self):
        self.assertEqual(
            error_code(
                lambda: resolver().resolve(
                    role="analysis_primary",
                    requested_user_tier="maximum",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "MAXIMUM_REQUIRES_EXPLICIT_SELECTION",
        )

    def test_thinking_off_is_not_low_and_does_not_change_tier(self):
        result = resolver().resolve(
            role="analysis_primary",
            requested_user_tier="high_quality",
            thinking_enabled=False,
            data_classification="public",
            explicit_selection=False,
            at=RESOLVE_AT,
        )
        self.assertEqual(result["profile_id"], "analysis_high_quality_v1")
        self.assertEqual(result["native_effort"], "disabled")
        self.assertEqual(result["native_projection"], {"thinking": "disabled"})

    def test_thinking_off_unsupported_fails_closed(self):
        profiles = model_profiles()
        profiles[0]["thinking_can_be_disabled"] = False
        profiles[0]["native_effort_projections"].pop("disabled")
        policy = tier_policy()
        policy["roles"]["analysis_primary"]["economy"]["thinking_off_native_effort"] = None
        self.assertEqual(
            error_code(
                lambda: resolver(policy=policy, profiles=profiles).resolve(
                    role="analysis_primary",
                    requested_user_tier="economy",
                    thinking_enabled=False,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "THINKING_OFF_UNSUPPORTED",
        )

    def test_non_reasoning_profile_is_not_applicable_and_on_is_unsupported(self):
        profiles = model_profiles()
        profile = profiles[0]
        profile["thinking_supported"] = False
        profile["thinking_can_be_disabled"] = False
        profile["supported_native_efforts"] = []
        profile["native_effort_order"] = []
        profile["native_default_effort"] = None
        profile["native_effort_projections"] = {"not_applicable": {"status": "NOT_APPLICABLE"}}
        policy = tier_policy()
        entry = policy["roles"]["analysis_primary"]["economy"]
        entry["thinking_on_native_effort"] = None
        entry["thinking_off_native_effort"] = None
        r = resolver(policy=policy, profiles=profiles)
        off = r.resolve(
            role="analysis_primary",
            requested_user_tier="economy",
            thinking_enabled=False,
            data_classification="public",
            explicit_selection=False,
            at=RESOLVE_AT,
        )
        self.assertEqual(off["thinking_status"], "NOT_APPLICABLE")
        self.assertEqual(
            error_code(
                lambda: r.resolve(
                    role="analysis_primary",
                    requested_user_tier="economy",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "THINKING_UNSUPPORTED",
        )

    def test_economy_requires_known_fresh_price(self):
        profiles = model_profiles()
        profiles[0]["price_status"] = "UNKNOWN"
        profiles[0]["price_input"] = None
        profiles[0]["price_output"] = None
        r = resolver(profiles=profiles)
        self.assertEqual(
            error_code(
                lambda: r.resolve(
                    role="analysis_primary",
                    requested_user_tier="economy",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "TIER_PRICE_UNKNOWN_NOT_ECONOMY",
        )

    def test_policy_and_native_capability_freshness_fail_closed(self):
        stale_policy = tier_policy()
        stale_policy["expires_at"] = "2026-08-10T00:00:00+00:00"
        self.assertEqual(
            error_code(
                lambda: resolver(policy=stale_policy).resolve(
                    role="analysis_primary",
                    requested_user_tier="standard",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "TIER_POLICY_STALE",
        )
        profiles = model_profiles()
        profiles[1]["capability_expires_at"] = "2026-08-10T00:00:00+00:00"
        self.assertEqual(
            error_code(
                lambda: resolver(profiles=profiles).resolve(
                    role="analysis_primary",
                    requested_user_tier="standard",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "NATIVE_EFFORT_CAPABILITY_STALE",
        )

    def test_missing_provider_binding_and_qualification_fail_with_stable_codes(self):
        _, binding = verified_credential()
        missing_provider = TierResolver(
            policy=tier_policy(),
            profiles=model_profiles(),
            provider_presets=[],
            credential_bindings=[binding],
            resolver_revision="r1",
        )
        self.assertEqual(
            error_code(
                lambda: missing_provider.resolve(
                    role="analysis_primary",
                    requested_user_tier="standard",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "PROVIDER_PRESET_UNKNOWN",
        )
        missing_binding = TierResolver(
            policy=tier_policy(),
            profiles=model_profiles(),
            provider_presets=[provider_preset()],
            credential_bindings=[],
            resolver_revision="r1",
        )
        self.assertEqual(
            error_code(
                lambda: missing_binding.resolve(
                    role="analysis_primary",
                    requested_user_tier="standard",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "CREDENTIAL_REF_MISSING",
        )
        profiles = model_profiles()
        profiles[1]["qualification_status"] = "NOT_ASSESSED"
        self.assertEqual(
            error_code(
                lambda: resolver(profiles=profiles).resolve(
                    role="analysis_primary",
                    requested_user_tier="standard",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "QUALIFICATION_NOT_ASSESSED",
        )

    def test_privacy_boundary_precedes_selected_tier(self):
        self.assertEqual(
            error_code(
                lambda: resolver().resolve(
                    role="analysis_primary",
                    requested_user_tier="maximum",
                    thinking_enabled=True,
                    data_classification="entrusted",
                    explicit_selection=True,
                    at=RESOLVE_AT,
                )
            ),
            "DATA_BOUNDARY_BLOCKS_ROUTE",
        )

    def test_unverified_credential_blocks_resolution(self):
        store, binding = verified_credential()
        binding = store.replace(
            binding["credential_ref"],
            secret_value="replacement-fixture-never-sent",
            rotated_at=RESOLVE_AT,
        )
        self.assertEqual(
            error_code(
                lambda: resolver(binding=binding).resolve(
                    role="analysis_primary",
                    requested_user_tier="standard",
                    thinking_enabled=True,
                    data_classification="public",
                    explicit_selection=False,
                    at=RESOLVE_AT,
                )
            ),
            "CONNECTION_NOT_VERIFIED",
        )

    def test_disclosed_collapse_is_visible_in_result(self):
        result = resolver(policy=disclosed_collapse_policy()).resolve(
            role="analysis_primary",
            requested_user_tier="high_quality",
            thinking_enabled=True,
            data_classification="public",
            explicit_selection=False,
            at=RESOLVE_AT,
        )
        self.assertEqual(result["resolution_status"], "COLLAPSED_DISCLOSED")
        self.assertEqual(result["collapse_disclosure"]["collapse_target"], "standard")


class ConfigAndSchemaTests(unittest.TestCase):
    def test_config_merge_is_deterministic_and_keeps_credential_out_of_overlay(self):
        first = merge_configuration(ordinary_config(), advanced_config())
        second = merge_configuration(ordinary_config(), advanced_config())
        self.assertEqual(first, second)
        self.assertEqual(first["effective"]["credential_binding_id"], "binding_main")
        bad = {**advanced_config(), "credential_ref": "REF:FAKE:binding_main:v1"}
        self.assertEqual(error_code(lambda: validate_advanced_config(bad)), "SECRET_VALUE_FIELD_FORBIDDEN")
        nested = advanced_config()
        nested["budget"]["token"] = "not-allowed"
        self.assertEqual(error_code(lambda: validate_advanced_config(nested)), "SECRET_VALUE_FIELD_FORBIDDEN")
        conflict = advanced_config()
        conflict["per_role_thinking"]["undeclared_role"] = True
        self.assertEqual(
            error_code(lambda: merge_configuration(ordinary_config(), conflict)),
            "CONFIG_OVERLAY_CONFLICT",
        )

    def test_redaction_and_forbidden_value_scan_cover_nested_support_projection(self):
        fake = "fixture-value-never-sent"
        value = {"doctor": {"api_key": fake, "credential_ref": "REF:FAKE:binding_main:v1"}}
        redacted = redact_public(value)
        self.assertEqual(redacted["doctor"]["api_key"], "[REDACTED]")
        self.assertEqual(redacted["doctor"]["credential_ref"], "REF:FAKE:binding_main:v1")
        assert_forbidden_values_absent(redacted, [fake])
        self.assertEqual(
            error_code(lambda: assert_forbidden_values_absent(value, [fake])),
            "SECRET_VALUE_LEAK_DETECTED",
        )

    def test_error_catalog_projects_to_the_error_envelope_without_raw_details(self):
        required = {
            "CREDENTIAL_REF_MISSING",
            "CREDENTIAL_BACKEND_UNAVAILABLE",
            "CREDENTIAL_REVOKED",
            "CONNECTION_NOT_VERIFIED",
            "PROVIDER_PRESET_UNKNOWN",
            "TIER_POLICY_STALE",
            "TIER_PRICE_UNKNOWN_NOT_ECONOMY",
            "TIER_PROFILE_UNAVAILABLE",
            "TIER_COLLAPSE_NOT_DECLARED",
            "NATIVE_EFFORT_UNSUPPORTED",
            "NATIVE_EFFORT_CAPABILITY_STALE",
            "MAXIMUM_REQUIRES_EXPLICIT_SELECTION",
            "ROLE_MAPPING_UNRESOLVED",
            "QUALIFICATION_NOT_ASSESSED",
            "THINKING_UNSUPPORTED",
            "THINKING_NOT_APPLICABLE",
            "DATA_BOUNDARY_BLOCKS_ROUTE",
            "FALLBACK_REQUIRES_EXPLICIT_POLICY",
            "RETURNED_MODEL_IDENTITY_DRIFT",
            "CONFIG_OVERLAY_CONFLICT",
        }
        self.assertTrue(required.issubset(ERROR_CODE_CATALOG))
        marker = "raw-detail-must-not-leak"
        envelope = configuration_error_envelope(
            ModelConfigurationError("TIER_PROFILE_UNAVAILABLE", [marker]),
            correlation_id="corr-001",
        )
        serialized = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        self.assertEqual(envelope["schema_version"], "ErrorEnvelope-v1")
        self.assertEqual(envelope["error_code"], "TIER_PROFILE_UNAVAILABLE")
        self.assertNotIn(marker, serialized)

    def test_legacy_env_compatibility_projects_name_without_reading_value(self):
        projected = legacy_env_name_reference("SYNTHETIC_API_KEY")
        self.assertEqual(projected["credential_ref"], "REF:LEGACY_ENV:SYNTHETIC_API_KEY")
        self.assertFalse(projected["secret_value_read"])
        self.assertFalse(projected["resolvable_in_analysis"])
        self.assertEqual(
            error_code(lambda: legacy_env_name_reference("not-valid")),
            "LEGACY_ENV_NAME_INVALID",
        )

    def test_all_json_schemas_are_valid_and_accept_public_examples(self):
        schemas = {}
        for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path.name] = schema
        self.assertEqual(len(schemas), 9)
        store, binding = verified_credential()
        receipt = check_connection(
            store=store,
            credential_ref=binding["credential_ref"],
            provider_preset=provider_preset(),
            provider=FakeProvider("provider_fake"),
            requested_model_alias="model-fast",
            check_id="check-schema",
            checked_at=RESOLVE_AT,
        )
        result = resolver(binding=store.metadata(binding["credential_ref"])).resolve(
            role="analysis_primary",
            requested_user_tier="economy",
            thinking_enabled=True,
            data_classification="public",
            explicit_selection=False,
            at=RESOLVE_AT,
        )
        examples = {
            "credential_binding_v1.schema.json": store.metadata(binding["credential_ref"]),
            "provider_preset_v1.schema.json": provider_preset(),
            "model_profile_capability_v1.schema.json": model_profiles()[0],
            "tier_policy_manifest_v1.schema.json": tier_policy(),
            "ordinary_config_v1.schema.json": ordinary_config(),
            "advanced_config_boundary_v1.schema.json": advanced_config(),
            "connection_check_receipt_v1.schema.json": receipt,
            "tier_resolution_v1.schema.json": result,
            "legacy_env_reference_v1.schema.json": legacy_env_name_reference("SYNTHETIC_API_KEY"),
        }
        for name, example in examples.items():
            with self.subTest(schema=name):
                Draft202012Validator(schemas[name], format_checker=FormatChecker()).validate(example)


if __name__ == "__main__":
    unittest.main()
