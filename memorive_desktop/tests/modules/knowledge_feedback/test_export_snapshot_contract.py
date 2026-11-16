from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import memorive.sessions.refinement as conversation_refinement
from memorive.sessions.refinement.contracts import ConversationContractError
from memorive.sessions.refinement.contracts import validate_export_snapshot, validate_source_access_envelope
from memorive.sessions.refinement.contracts import make_capture_attempt_receipt, make_source_access_envelope, payload_identity, validate_capture_attempt_receipt
from memorive.sessions.refinement.provider_adapter import CLAUDE_CODE_LOCAL_PROFILE, CODEX_APP_THREAD_BRIDGE_PROFILE, CODEX_LOCAL_PROFILE, DEEPSEEK_EDGE_VISIBLE_UI_PROFILE
from memorive.sessions.refinement.provider_adapter import build_readonly_thread_bridge_snapshot, build_visible_ui_thread_bridge_snapshot
from memorive.sessions.refinement.provider_adapter import build_synthetic_snapshot, capture_local_jsonl_snapshot, profile_admission
from memorive.sessions.refinement.graph import build_normalized_graph
NOW = "2026-08-18T01:00:00+09:00"


def message(
    message_id: str,
    text: str,
    *,
    parent_id: str | None = None,
    role: str = "user",
) -> dict:
    return {
        "message_id": message_id,
        "parent_id": parent_id,
        "role": role,
        "blocks": [{"type": "text", "text": text, "metadata": {}}],
        "created_at": NOW,
        "status": "complete",
        "edit_of": None,
        "regeneration_group": None,
    }


def make_export(conversations: list[dict] | None = None) -> dict:
    if conversations is None:
        conversations = [
            {
                "conversation_id": "conversation-001",
                "title": "synthetic fixture",
                "messages": [message("message-001", "METHOD: preserve exact lineage")],
                "attachments": [],
            }
        ]
    return {
        "provider_profile": "GENERIC_SYNTHETIC_EXPORT_V1",
        "conversations": conversations,
        "archive_members": ["conversations.json"],
    }


def make_envelope(*, conversations: int = 10, messages: int = 100) -> dict:
    return make_source_access_envelope(
        source_id="synthetic-source-001",
        provider="GENERIC_SYNTHETIC",
        surface="SYNTHETIC_FIXTURE",
        access_mode="SYNTHETIC_ISOMORPHIC_FIXTURE",
        locator="fixture://capabilities/t12/synthetic",
        scope_mode="SELECTED_CONVERSATIONS",
        ceiling={
            "conversations": conversations,
            "messages": messages,
            "attachment_metadata": 20,
            "attachment_body_reads": 0,
            "content_bytes": 1024 * 1024,
        },
        ownership="self",
        classification="PUBLIC_SAFE_SYNTHETIC",
        retention="RUN_SCOPED",
        created_at=NOW,
    )


def make_thread_bridge_envelope(
    *,
    messages: int = 20,
    access_mode: str = "PROVIDER_OFFICIAL_API_READ",
) -> dict:
    return make_source_access_envelope(
        source_id="official-thread-source-001",
        provider="OPENAI_CODEX_APP",
        surface="CODEX_APP_READONLY_THREAD_BRIDGE",
        access_mode=access_mode,
        locator="codex-app://thread/selected",
        scope_mode="SELECTED_CONVERSATIONS",
        ceiling={
            "conversations": 1,
            "messages": messages,
            "attachment_metadata": 0,
            "attachment_body_reads": 0,
            "content_bytes": 1024 * 1024,
        },
        ownership="self",
        classification="PRIVATE_CONVERSATION",
        retention="RUN_SCOPED",
        created_at=NOW,
    )


def make_thread_bridge_bundle(*, source_equals_capture_actor: bool = False) -> dict:
    return {
        "provider_profile": CODEX_APP_THREAD_BRIDGE_PROFILE,
        "thread_kind": "chatgpt",
        "thread_id": "chatgpt-thread-001",
        "capture_actor_id": (
            "chatgpt-thread-001"
            if source_equals_capture_actor
            else "codex-executor-thread-001"
        ),
        "source_equals_capture_actor": source_equals_capture_actor,
        "messages": [
            {
                "message_id": "message-001",
                "role": "user",
                "text": "DECISION: retain the bounded official thread bridge",
                "created_at": NOW,
            },
            {
                "message_id": "message-002",
                "role": "assistant",
                "text": "METHOD: normalize without reading browser storage",
                "created_at": NOW,
            },
        ],
        "has_more": True,
        "source_truncated": True,
    }


def make_deepseek_visible_envelope(*, messages: int = 20) -> dict:
    return make_source_access_envelope(
        source_id="deepseek-visible-source-001",
        provider="DEEPSEEK_WEB",
        surface="EDGE_BROWSER_VISIBLE_UI",
        access_mode="VISIBLE_AUTHENTICATED_UI_READONLY_CAPTURE",
        locator="edge-visible://deepseek/selected-conversation",
        scope_mode="SELECTED_CONVERSATIONS",
        ceiling={
            "conversations": 1,
            "messages": messages,
            "attachment_metadata": 0,
            "attachment_body_reads": 0,
            "content_bytes": 1024 * 1024,
        },
        ownership="self",
        classification="PRIVATE_CONVERSATION",
        retention="RUN_SCOPED",
        created_at=NOW,
    )


def make_deepseek_visible_bundle() -> dict:
    return {
        "provider_profile": DEEPSEEK_EDGE_VISIBLE_UI_PROFILE,
        "conversation_id": "deepseek-visible-conversation-001",
        "messages": [
            {
                "message_id": "message-001",
                "role": "user",
                "text": "OPEN_QUESTION: what is the first train?",
            },
            {
                "message_id": "message-002",
                "role": "assistant",
                "text": "AI_SUGGESTION: verify the published timetable.",
            },
        ],
        "observed_at": NOW,
        "source_truncated": True,
        "boundary_method": "HOST_OBSERVED_VISIBLE_UI_TURN_ORDER_V1",
    }


def make_snapshot(export: dict | None = None) -> dict:
    return build_synthetic_snapshot(
        snapshot_id="snapshot-001",
        envelope=make_envelope(),
        export=export or make_export(),
        captured_at=NOW,
    )


class ExportSnapshotContractTests(unittest.TestCase):
    def test_package_exports_controlled_capture_contracts(self) -> None:
        self.assertIs(
            conversation_refinement.capture_local_jsonl_snapshot,
            capture_local_jsonl_snapshot,
        )
        self.assertIs(
            conversation_refinement.make_capture_attempt_receipt,
            make_capture_attempt_receipt,
        )
        self.assertIs(
            conversation_refinement.validate_capture_attempt_receipt,
            validate_capture_attempt_receipt,
        )
        self.assertEqual(conversation_refinement.CODEX_LOCAL_PROFILE, CODEX_LOCAL_PROFILE)
        self.assertEqual(
            conversation_refinement.CLAUDE_CODE_LOCAL_PROFILE,
            CLAUDE_CODE_LOCAL_PROFILE,
        )
        self.assertEqual(
            conversation_refinement.CODEX_APP_THREAD_BRIDGE_PROFILE,
            CODEX_APP_THREAD_BRIDGE_PROFILE,
        )
        self.assertIs(
            conversation_refinement.build_readonly_thread_bridge_snapshot,
            build_readonly_thread_bridge_snapshot,
        )

    def test_bounded_envelope_and_snapshot_are_immutable(self) -> None:
        envelope = validate_source_access_envelope(make_envelope())
        snapshot = validate_export_snapshot(make_snapshot())
        self.assertFalse(envelope["credential_cookie_storage_reads_allowed"])
        self.assertFalse(envelope["source_mutation_allowed"])
        self.assertFalse(envelope["formal_ingest_authorized"])
        self.assertTrue(snapshot["immutable"])
        self.assertFalse(snapshot["raw_payload_git_eligible"])
        self.assertTrue(snapshot["synthetic_fixture"])
        self.assertEqual(snapshot["conversation_count"], 1)
        self.assertEqual(snapshot["message_count"], 1)
        self.assertEqual(snapshot["attachment_count"], 0)

    def test_edited_snapshot_is_read_and_a_missing_or_malformed_hash_is_refused(self) -> None:
        snapshot = make_snapshot()
        snapshot["conversation_count"] = 99  # the stored hash is not recomputed on read
        self.assertEqual(validate_export_snapshot(snapshot)["conversation_count"], 99)
        malformed = dict(snapshot, content_hash=dict(snapshot["content_hash"], value="not-a-sha256"))
        with self.assertRaisesRegex(ConversationContractError, "HASH_INVALID"):
            validate_export_snapshot(malformed)
        missing = {key: value for key, value in snapshot.items() if key != "content_hash"}
        with self.assertRaisesRegex(ConversationContractError, "HASH_INVALID"):
            validate_export_snapshot(missing)

    def test_archive_path_escape_is_rejected(self) -> None:
        export = make_export()
        export["archive_members"] = ["../escape.json"]
        with self.assertRaisesRegex(ConversationContractError, "PATH_ESCAPE"):
            make_snapshot(export)

    def test_capture_ceiling_is_fail_closed(self) -> None:
        conversations = [
            {
                "conversation_id": "conversation-001",
                "title": "one",
                "messages": [message("document_processing", "METHOD: one"), message("evidence_extraction", "METHOD: two")],
                "attachments": [],
            }
        ]
        with self.assertRaisesRegex(ConversationContractError, "MESSAGE_CEILING_EXCEEDED"):
            build_synthetic_snapshot(
                snapshot_id="snapshot-ceiling",
                envelope=make_envelope(messages=1),
                export=make_export(conversations),
                captured_at=NOW,
            )

    def test_real_provider_profile_is_not_guessed(self) -> None:
        self.assertEqual(profile_admission("GENERIC_SYNTHETIC_EXPORT_V1"), "ADMITTED")
        self.assertEqual(
            profile_admission(CODEX_APP_THREAD_BRIDGE_PROFILE),
            "ADMITTED_WITH_LIMITS",
        )
        self.assertEqual(profile_admission("CHATGPT_VISIBLE_UI_CURRENT"), "PROFILE_REQUIRED")
        self.assertEqual(profile_admission("GEMINI_VISIBLE_UI_CURRENT"), "PROFILE_REQUIRED")
        self.assertEqual(profile_admission("DEEPSEEK_VISIBLE_UI_CURRENT"), "PROFILE_REQUIRED")
        self.assertEqual(
            profile_admission(DEEPSEEK_EDGE_VISIBLE_UI_PROFILE),
            "ADMITTED_WITH_LIMITS",
        )

    def test_observed_deepseek_visible_ui_bridge_is_bounded_and_graph_compatible(self) -> None:
        snapshot, export, observation = build_visible_ui_thread_bridge_snapshot(
            snapshot_id="deepseek-visible-snapshot",
            envelope=make_deepseek_visible_envelope(),
            bundle=make_deepseek_visible_bundle(),
            captured_at=NOW,
        )
        self.assertEqual(snapshot["provider_profile"], DEEPSEEK_EDGE_VISIBLE_UI_PROFILE)
        self.assertEqual(snapshot["message_count"], 2)
        self.assertFalse(snapshot["synthetic_fixture"])
        self.assertEqual(export["conversations"][0]["title"], "PRIVATE_TITLE_WITHHELD")
        self.assertTrue(observation["visible_only"])
        self.assertTrue(observation["source_truncated"])
        self.assertEqual(observation["credential_cookie_storage_reads"], 0)
        self.assertEqual(observation["source_mutations"], 0)
        graph = build_normalized_graph(
            graph_id="deepseek-visible-graph",
            snapshot=snapshot,
            export=export,
        )
        self.assertEqual(len(graph["nodes"]), 2)

    def test_visible_ui_bridge_fails_closed_on_unobserved_provider_drift_and_ceiling(self) -> None:
        with self.subTest(case="unobserved Gemini profile"):
            bundle = make_deepseek_visible_bundle()
            bundle["provider_profile"] = "GEMINI_EDGE_VISIBLE_UI_V1"
            with self.assertRaisesRegex(ConversationContractError, "VISIBLE_UI_PROFILE_NOT_ADMITTED"):
                build_visible_ui_thread_bridge_snapshot(
                    snapshot_id="gemini-unobserved",
                    envelope=make_deepseek_visible_envelope(),
                    bundle=bundle,
                    captured_at=NOW,
                )
        with self.subTest(case="unknown browser field"):
            bundle = make_deepseek_visible_bundle()
            bundle["cookie"] = "forbidden"
            with self.assertRaisesRegex(ConversationContractError, "VISIBLE_UI_BUNDLE_EXACT_KEYS_MISMATCH"):
                build_visible_ui_thread_bridge_snapshot(
                    snapshot_id="visible-ui-drift",
                    envelope=make_deepseek_visible_envelope(),
                    bundle=bundle,
                    captured_at=NOW,
                )
        with self.subTest(case="message ceiling"):
            with self.assertRaisesRegex(ConversationContractError, "CAPTURE_MESSAGE_CEILING_EXCEEDED"):
                build_visible_ui_thread_bridge_snapshot(
                    snapshot_id="visible-ui-ceiling",
                    envelope=make_deepseek_visible_envelope(messages=1),
                    bundle=make_deepseek_visible_bundle(),
                    captured_at=NOW,
                )

    def test_official_chatgpt_thread_bridge_is_bounded_nonself_and_graph_compatible(self) -> None:
        snapshot, export, observation = build_readonly_thread_bridge_snapshot(
            snapshot_id="chatgpt-thread-bridge-snapshot",
            envelope=make_thread_bridge_envelope(),
            bundle=make_thread_bridge_bundle(),
            captured_at=NOW,
        )
        self.assertEqual(snapshot["provider_profile"], CODEX_APP_THREAD_BRIDGE_PROFILE)
        self.assertEqual(snapshot["message_count"], 2)
        self.assertFalse(snapshot["synthetic_fixture"])
        self.assertEqual(export["conversations"][0]["title"], "PRIVATE_TITLE_WITHHELD")
        self.assertEqual(export["conversations"][0]["attachments"], [])
        self.assertTrue(observation["nonself_source"])
        self.assertTrue(observation["source_has_more"])
        self.assertTrue(observation["source_truncated"])
        self.assertEqual(observation["credential_cookie_storage_reads"], 0)
        self.assertEqual(observation["source_mutations"], 0)
        graph = build_normalized_graph(
            graph_id="chatgpt-thread-bridge-graph",
            snapshot=snapshot,
            export=export,
        )
        self.assertEqual(len(graph["nodes"]), 2)

    def test_official_thread_bridge_rejects_self_source(self) -> None:
        with self.assertRaisesRegex(
            ConversationContractError,
            "THREAD_BRIDGE_SELF_SOURCE_FORBIDDEN",
        ):
            build_readonly_thread_bridge_snapshot(
                snapshot_id="self-thread-bridge-snapshot",
                envelope=make_thread_bridge_envelope(),
                bundle=make_thread_bridge_bundle(source_equals_capture_actor=True),
                captured_at=NOW,
            )

    def test_official_thread_bridge_deterministically_trims_outer_message_whitespace(self) -> None:
        bundle = make_thread_bridge_bundle()
        bundle["messages"][1]["text"] = "\n  METHOD: normalize provider outer whitespace\t\n"
        _, export, _ = build_readonly_thread_bridge_snapshot(
            snapshot_id="outer-whitespace-normalization",
            envelope=make_thread_bridge_envelope(),
            bundle=bundle,
            captured_at=NOW,
        )
        self.assertEqual(
            export["conversations"][0]["messages"][1]["blocks"][0]["text"],
            "METHOD: normalize provider outer whitespace",
        )

    def test_official_thread_bridge_fails_closed_on_contract_drift_and_ceiling(self) -> None:
        with self.subTest(case="wrong access mode"):
            with self.assertRaisesRegex(
                ConversationContractError,
                "THREAD_BRIDGE_REQUIRES_OFFICIAL_API_READ_ENVELOPE",
            ):
                build_readonly_thread_bridge_snapshot(
                    snapshot_id="wrong-envelope",
                    envelope=make_thread_bridge_envelope(
                        access_mode="VISIBLE_AUTHENTICATED_UI_READONLY_CAPTURE"
                    ),
                    bundle=make_thread_bridge_bundle(),
                    captured_at=NOW,
                )
        with self.subTest(case="unknown bundle key"):
            drifted = make_thread_bridge_bundle()
            drifted["cookie"] = "forbidden"
            with self.assertRaisesRegex(
                ConversationContractError,
                "THREAD_BRIDGE_BUNDLE_EXACT_KEYS_MISMATCH",
            ):
                build_readonly_thread_bridge_snapshot(
                    snapshot_id="unknown-key",
                    envelope=make_thread_bridge_envelope(),
                    bundle=drifted,
                    captured_at=NOW,
                )
        with self.subTest(case="message ceiling"):
            with self.assertRaisesRegex(
                ConversationContractError,
                "CAPTURE_MESSAGE_CEILING_EXCEEDED",
            ):
                build_readonly_thread_bridge_snapshot(
                    snapshot_id="message-ceiling",
                    envelope=make_thread_bridge_envelope(messages=1),
                    bundle=make_thread_bridge_bundle(),
                    captured_at=NOW,
                )

    def test_local_jsonl_read_stops_at_exact_byte_ceiling(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bounded.jsonl"
            first = (
                '{"timestamp":"2026-08-18T00:00:01Z","type":"response_item",'
                '"payload":{"type":"message","role":"user","content":'
                '[{"type":"input_text","text":"METHOD: bounded first record"}]}}\n'
            ).encode("utf-8")
            second = (
                '{"timestamp":"2026-08-18T00:00:02Z","type":"response_item",'
                '"payload":{"type":"message","role":"assistant","content":'
                '[{"type":"output_text","text":"' + ("x" * 4096) + '"}]}}\n'
            ).encode("utf-8")
            source.write_bytes(first + second)
            envelope = make_source_access_envelope(
                source_id="codex-byte-ceiling",
                provider="OPENAI_CODEX",
                surface="CODEX_LOCAL_SESSION",
                access_mode="AUTHORIZED_LOCAL_CLI_SESSION_ROOT",
                locator=str(root.resolve()),
                scope_mode="SELECTED_CONVERSATIONS",
                ceiling={
                    "conversations": 1,
                    "messages": 20,
                    "attachment_metadata": 10,
                    "attachment_body_reads": 0,
                    "content_bytes": 2048,
                },
                ownership="self",
                classification="PRIVATE_CONVERSATION",
                retention="RUN_SCOPED",
                created_at=NOW,
            )
            snapshot, _, observation = capture_local_jsonl_snapshot(
                snapshot_id="codex-byte-ceiling-snapshot",
                envelope=envelope,
                source_root=root,
                source_path=source,
                provider_profile=CODEX_LOCAL_PROFILE,
                captured_at=NOW,
            )
            self.assertEqual(snapshot["message_count"], 1)
            self.assertEqual(observation["source_bytes_read"], 2048)
            self.assertEqual(observation["stop_reason"], "CEILING_REACHED")

    def test_claude_meta_and_tool_result_records_are_not_messages(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "claude-filtering.jsonl"
            source.write_text(
                "\n".join(
                    [
                        '{"sessionId":"s","timestamp":"2026-08-18T00:00:00Z","isMeta":true,"message":{"role":"user","content":"meta must be ignored"}}',
                        '{"sessionId":"s","timestamp":"2026-08-18T00:00:01Z","type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"tool payload must not become a user claim"}]}}',
                        '{"sessionId":"s","timestamp":"2026-08-18T00:00:02Z","type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"METHOD: retain only visible dialogue text"}]}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            envelope = make_source_access_envelope(
                source_id="claude-filtering",
                provider="ANTHROPIC_CLAUDE_CODE",
                surface="CLAUDE_CODE_LOCAL_SESSION",
                access_mode="AUTHORIZED_LOCAL_CLI_SESSION_ROOT",
                locator=str(root.resolve()),
                scope_mode="SELECTED_CONVERSATIONS",
                ceiling={
                    "conversations": 1,
                    "messages": 20,
                    "attachment_metadata": 10,
                    "attachment_body_reads": 0,
                    "content_bytes": 4096,
                },
                ownership="self",
                classification="PRIVATE_CONVERSATION",
                retention="RUN_SCOPED",
                created_at=NOW,
            )
            snapshot, export, _ = capture_local_jsonl_snapshot(
                snapshot_id="claude-filtering-snapshot",
                envelope=envelope,
                source_root=root,
                source_path=source,
                provider_profile=CLAUDE_CODE_LOCAL_PROFILE,
                captured_at=NOW,
            )
            self.assertEqual(snapshot["message_count"], 1)
            self.assertEqual(snapshot["attachment_count"], 0)
            self.assertEqual(export["conversations"][0]["messages"][0]["role"], "assistant")

    def test_envelope_cannot_enable_credential_or_formal_ingest(self) -> None:
        for field in ("credential_cookie_storage_reads_allowed", "formal_ingest_authorized"):
            with self.subTest(field=field):
                envelope = deepcopy(make_envelope())
                envelope.pop("content_hash")
                envelope[field] = True
                from memorive.research.knowledge_feedback.canonical import make_hashed_payload

                with self.assertRaises(ConversationContractError):
                    validate_source_access_envelope(make_hashed_payload(envelope))

    def test_public_safe_capture_receipt_replays_counts_and_zero_side_effects(self) -> None:
        envelope = make_envelope()
        snapshot = make_snapshot()
        receipt = make_capture_attempt_receipt(
            receipt_id="capture-receipt-001",
            envelope=envelope,
            provider_profile="GENERIC_SYNTHETIC_EXPORT_V1",
            required_source=False,
            input_controller_class="PUBLIC_SYNTHETIC",
            capture_transport="SYNTHETIC_FIXTURE",
            private_custody_locator_hash=payload_identity({"locator": "private://run/capture-001"}),
            snapshot_hash=snapshot["content_hash"],
            conversation_count=snapshot["conversation_count"],
            message_count=snapshot["message_count"],
            attachment_metadata_count=snapshot["attachment_count"],
            content_bytes=snapshot["source_payload_bytes"],
            stop_reason="SELECTED_SCOPE_COMPLETE",
            terminal_status="PASS",
            created_at=NOW,
        )
        checked = validate_capture_attempt_receipt(receipt)
        self.assertTrue(checked["ceiling_replay_pass"])
        self.assertTrue(checked["redaction_before_external_model"])
        self.assertEqual(set(checked["side_effect_counters"].values()), {0})

    def test_official_thread_bridge_capture_receipt_is_admitted_with_existing_ceiling_stop(self) -> None:
        envelope = make_thread_bridge_envelope()
        snapshot, _, _ = build_readonly_thread_bridge_snapshot(
            snapshot_id="official-bridge-receipt-snapshot",
            envelope=envelope,
            bundle=make_thread_bridge_bundle(),
            captured_at=NOW,
        )
        receipt = make_capture_attempt_receipt(
            receipt_id="official-bridge-receipt-001",
            envelope=envelope,
            provider_profile=CODEX_APP_THREAD_BRIDGE_PROFILE,
            required_source=True,
            input_controller_class="USER_ACCOUNT_OFFICIAL_THREAD_BRIDGE",
            capture_transport="OFFICIAL_THREAD_READONLY_BRIDGE",
            private_custody_locator_hash=payload_identity(
                {"locator": "private://run/official-thread-bridge"}
            ),
            snapshot_hash=snapshot["content_hash"],
            conversation_count=snapshot["conversation_count"],
            message_count=snapshot["message_count"],
            attachment_metadata_count=snapshot["attachment_count"],
            content_bytes=snapshot["source_payload_bytes"],
            stop_reason="CEILING_REACHED",
            terminal_status="PASS",
            created_at=NOW,
        )
        checked = validate_capture_attempt_receipt(receipt)
        self.assertEqual(
            checked["input_controller_class"],
            "USER_ACCOUNT_OFFICIAL_THREAD_BRIDGE",
        )
        self.assertEqual(
            checked["capture_transport"],
            "OFFICIAL_THREAD_READONLY_BRIDGE",
        )
        self.assertFalse(checked["task_terminal_credit"])

    def test_reauth_blocker_is_distinct_from_capture_pass(self) -> None:
        receipt = make_capture_attempt_receipt(
            receipt_id="capture-receipt-reauth",
            envelope=make_envelope(),
            provider_profile="PROFILE_REQUIRED",
            required_source=True,
            input_controller_class="USER_ACCOUNT_VISIBLE_UI",
            capture_transport="VISIBLE_UI_READONLY",
            private_custody_locator_hash=None,
            snapshot_hash=None,
            conversation_count=0,
            message_count=0,
            attachment_metadata_count=0,
            content_bytes=0,
            stop_reason="BLOCKED_USER_REAUTH_REQUIRED",
            terminal_status="BLOCKED",
            created_at=NOW,
        )
        self.assertEqual(receipt["terminal_status"], "BLOCKED")
        self.assertFalse(receipt["task_terminal_credit"])
        self.assertIsNone(receipt["snapshot_hash"])

    def test_codex_local_profile_is_bounded_and_graph_compatible(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "session.jsonl"
            source.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-08-18T00:00:00Z","type":"session_meta","payload":{"id":"codex-session","cwd":"/tmp/project"}}',
                        '{"timestamp":"2026-08-18T00:00:01Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"METHOD: exact local read"}]}}',
                        '{"timestamp":"2026-08-18T00:00:02Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"PROVENANCE: keep source identity"}]}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            envelope = make_source_access_envelope(
                source_id="codex-local-source",
                provider="OPENAI_CODEX",
                surface="CODEX_LOCAL_SESSION",
                access_mode="AUTHORIZED_LOCAL_CLI_SESSION_ROOT",
                locator=str(root.resolve()),
                scope_mode="SELECTED_CONVERSATIONS",
                ceiling={
                    "conversations": 1,
                    "messages": 20,
                    "attachment_metadata": 10,
                    "attachment_body_reads": 0,
                    "content_bytes": 4096,
                },
                ownership="self",
                classification="PRIVATE_CONVERSATION",
                retention="RUN_SCOPED",
                created_at=NOW,
            )
            snapshot, export, observation = capture_local_jsonl_snapshot(
                snapshot_id="codex-local-snapshot",
                envelope=envelope,
                source_root=root,
                source_path=source,
                provider_profile=CODEX_LOCAL_PROFILE,
                captured_at=NOW,
            )
            self.assertEqual(profile_admission(CODEX_LOCAL_PROFILE), "ADMITTED_WITH_LIMITS")
            self.assertEqual(snapshot["message_count"], 2)
            self.assertLessEqual(observation["source_bytes_read"], 4096)
            graph = build_normalized_graph(graph_id="codex-local-graph", snapshot=snapshot, export=export)
            self.assertEqual(len(graph["nodes"]), 2)

    def test_claude_local_profile_and_root_containment(self) -> None:
        with TemporaryDirectory() as allowed, TemporaryDirectory() as outside:
            root = Path(allowed)
            source = root / "claude.jsonl"
            source.write_text(
                "\n".join(
                    [
                        '{"sessionId":"claude-session","timestamp":"2026-08-18T00:00:01Z","type":"user","message":{"role":"user","content":"DECISION: local capture only"}}',
                        '{"sessionId":"claude-session","timestamp":"2026-08-18T00:00:02Z","type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"METHOD: bounded provider adapter"}]}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            envelope = make_source_access_envelope(
                source_id="claude-local-source",
                provider="ANTHROPIC_CLAUDE_CODE",
                surface="CLAUDE_CODE_LOCAL_SESSION",
                access_mode="AUTHORIZED_LOCAL_CLI_SESSION_ROOT",
                locator=str(root.resolve()),
                scope_mode="SELECTED_CONVERSATIONS",
                ceiling={
                    "conversations": 1,
                    "messages": 20,
                    "attachment_metadata": 10,
                    "attachment_body_reads": 0,
                    "content_bytes": 4096,
                },
                ownership="self",
                classification="PRIVATE_CONVERSATION",
                retention="RUN_SCOPED",
                created_at=NOW,
            )
            snapshot, export, _ = capture_local_jsonl_snapshot(
                snapshot_id="claude-local-snapshot",
                envelope=envelope,
                source_root=root,
                source_path=source,
                provider_profile=CLAUDE_CODE_LOCAL_PROFILE,
                captured_at=NOW,
            )
            self.assertEqual(snapshot["message_count"], 2)
            self.assertEqual(len(build_normalized_graph(graph_id="claude-local-graph", snapshot=snapshot, export=export)["nodes"]), 2)

            escaped = Path(outside) / "escape.jsonl"
            escaped.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(ConversationContractError, "OUTSIDE_FROZEN_ROOT"):
                capture_local_jsonl_snapshot(
                    snapshot_id="escaped",
                    envelope=envelope,
                    source_root=root,
                    source_path=escaped,
                    provider_profile=CLAUDE_CODE_LOCAL_PROFILE,
                    captured_at=NOW,
                )


if __name__ == "__main__":
    unittest.main()
