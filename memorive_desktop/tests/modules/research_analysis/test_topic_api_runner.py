"""RESEARCH_ANALYSIS cross-topic thin API runner contract tests.

These tests keep Research topic-analysis semantics above the mature MODEL_GATEWAY model
transport.  No test sends a network request.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from research_analysis.research_handoff_research_analysis_cross_topic_api_runner import CrossTopicRunResult, CrossTopicRunnerError, FrozenTextArtifact, FrozenTopicRequest, build_cross_topic_payload, normalize_cross_topic_identity_metadata, render_cross_topic_artifacts, render_cross_topic_markdown, run_cross_topic_analysis
def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["probe_id", "status"],
    "properties": {
        "probe_id": {"type": "string"},
        "status": {"const": "ok"},
    },
}


class CrossTopicApiRunnerTests(unittest.TestCase):
    def request(self) -> FrozenTopicRequest:
        text = (
            "template_id: Memorive-RESEARCH-HANDOFF-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST\n"
            "template_revision: r1.0\n"
            "request_id: HANDOFF-PROBE-REQUEST\n"
            "# 自定义问题 / 专题分析任务单\n"
            "核心问题：请仅根据冻结材料返回最小 JSON 验证对象。\n"
        )
        return FrozenTopicRequest(
            request_id="HANDOFF-PROBE-REQUEST",
            template_id="Memorive-RESEARCH-HANDOFF-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST",
            template_revision="r1.0",
            text=text,
            sha256=_sha256_text(text),
            data_ownership="self",
        )

    def artifact(self) -> FrozenTextArtifact:
        text = "source_id=ProbePaper#c0001; frozen fact: alpha precedes beta."
        return FrozenTextArtifact(
            artifact_id="PROBE-CARD-001",
            paper_id="PROBE-RETRIEVAL",
            artifact_type="CARD",
            evidence_depth="CARD_ONLY",
            text=text,
            sha256=_sha256_text(text),
        )

    def formal_request(self) -> FrozenTopicRequest:
        content_sha256 = "F" * 64
        text = (
            "template_id: Memorive-RESEARCH-HANDOFF-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST\n"
            "template_revision: r1.0\n"
            "request_id: HANDOFF-PROBE-REQUEST\n"
            "# 自定义问题 / 专题分析任务单\n"
            f"- request_content_sha256：{content_sha256}\n"
            "核心问题：请仅根据冻结材料返回专题 JSON 验证对象。\n"
        )
        return FrozenTopicRequest(
            request_id="HANDOFF-PROBE-REQUEST",
            template_id="Memorive-RESEARCH-HANDOFF-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST",
            template_revision="r1.0",
            text=text,
            sha256=_sha256_text(text),
            data_ownership="self",
            content_sha256=content_sha256,
        )

    def response(self, output: dict | None = None) -> dict:
        value = output or {"probe_id": "transport", "status": "ok"}
        return {
            "text": json.dumps(value, ensure_ascii=False),
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "stop_reason": "end_turn",
            "transport": {
                "stream": True,
                "complete": True,
                "last_event_type": "message_stop",
            },
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    def topic_output(self) -> dict:
        request = self.request()
        evidence_ref = {
            "paper_id": "G1-RETRIEVAL",
            "artifact_type": "CARD",
            "source_id": "ProbePaper#c0001",
            "artifact_sha256": "A" * 64,
            "evidence_depth": "CARD_PLUS_SINGLE_ANALYSIS",
        }
        section_ids = (
            "scope_and_evidence_boundary",
            "comparison_dimensions",
            "evolution_path",
            "consensus_and_continuities",
            "differences_and_tensions",
            "mechanisms_and_tradeoffs",
            "limitations_and_uncertainties",
            "recheck_recommendations",
        )
        return {
            "schema_version": "memorive.research.analysis.cross-topic-analysis.v2",
            "artifact_id": "HANDOFF-G1-CROSS-TOPIC-ANALYSIS",
            "group_id": "G1",
            "title": "G1 四篇论文专题分析",
            "primary_question": "冻结四篇材料中的技术演变如何形成连续关系，并保留哪些不可直接比较的边界？",
            "executive_answer": "冻结证据支持按问题重定义、机制承接与比较边界组织演变链；结论仅用于专题复核，不替用户作最终科研判断。",
            "execution": {
                "provider": "anthropic",
                "analysis_model": "claude-sonnet-5",
                "thinking_type": "adaptive",
                "effort": "xhigh",
                "max_tokens": 12288,
                "analyzed_at": "2026-08-09T20:00:00+09:00",
                "source_commit": "4d1209f151435098ffeabf293812b65f6bbd6105",
                "source_tree_manifest_sha256": "B" * 64,
                "request_id": request.request_id,
                "request_template_id": request.template_id,
                "request_template_revision": request.template_revision,
                "request_content_sha256": request.sha256,
                "call_id": "HANDOFF-G1-SONNET-SUBJECT-001",
            },
            "evidence_scope": {
                "input_snapshot_id": "HANDOFF-INPUT-SNAPSHOT-S006",
                "input_manifest_sha256": "C" * 64,
                "papers": [
                    {
                        "paper_id": f"G1-quality{index}",
                        "canonical_paper_id": f"ProbePaper{index}",
                        "version_note": f"frozen version {index}",
                        "evidence_depth": (
                            "CARD_ONLY" if index == 3 else "CARD_PLUS_SINGLE_ANALYSIS"
                        ),
                        "card_sha256": "A" * 64,
                        "analysis_sha256": "NONE" if index == 3 else "D" * 64,
                    }
                    for index in range(1, 5)
                ],
                "card_only_paper_id": "G1-PAPER-03",
                "card_only_analysis_absent": True,
            },
            "required_facet_coverage": [
                {
                    "facet_id": f"facet_{index}",
                    "covered": True,
                    "supporting_item_ids": [f"S0{index}-I01"],
                    "boundary_note": "仅在冻结证据深度内覆盖。",
                }
                for index in range(1, 6)
            ],
            "sections": [
                {
                    "section_id": section_id,
                    "heading": f"专题第 {index} 节",
                    "summary": "本节严格依据冻结专题任务单组织证据、关系与限定。",
                    "items": [
                        {
                            "item_id": f"S{index:02d}-I01",
                            "statement": "该陈述展示专题分析中的证据绑定与限定关系。",
                            "paper_ids": ["G1-RETRIEVAL"],
                            "evidence_refs": [dict(evidence_ref)],
                            "qualifiers": ["仅限冻结材料"],
                        }
                    ],
                }
                for index, section_id in enumerate(section_ids, start=1)
            ],
            "paper_trajectory": [
                {
                    "sequence": index,
                    "paper_id": f"G1-quality{index}",
                    "role": f"冻结演变阶段 {index}",
                    "transition_from_prior": "按专题问题保留该阶段与前序阶段的机制承接和比较边界。",
                    "evidence_refs": [dict(evidence_ref)],
                }
                for index in range(1, 5)
            ],
            "judgment_boundary": {
                "winner_selected": False,
                "overall_scientific_score_assigned": False,
                "final_user_judgment_made": False,
                "scientific_maturity": "NOT_ASSESSED",
            },
        }

    def topic_result(self, output: dict | None = None) -> CrossTopicRunResult:
        value = output or self.topic_output()
        raw_text = json.dumps(value, ensure_ascii=False)
        return CrossTopicRunResult(
            request_id=self.request().request_id,
            output=value,
            raw_text=raw_text,
            provider="anthropic",
            model="claude-sonnet-5",
            stop_reason="end_turn",
            transport={"stream": True, "complete": True},
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            prompt_sha256="E" * 64,
            response_sha256=_sha256_text(raw_text),
        )

    def test_payload_reuses_m9_user_message_path_without_native_schema(self) -> None:
        payload = build_cross_topic_payload(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
            thinking_type="adaptive",
            effort="xhigh",
            max_tokens=2048,
            timeout_seconds=120,
        )

        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["thinking"], {"type": "adaptive"})
        self.assertEqual(payload["effort"], "xhigh")
        self.assertEqual(payload["max_tokens"], 2048)
        self.assertTrue(payload["stream"])
        self.assertNotIn("system", payload)
        self.assertNotIn("output_config", payload)
        self.assertNotIn("response_format", payload)
        prompt = payload["messages"][0]["content"]
        self.assertIn("<FORMAL_TOPIC_REQUEST", prompt)
        self.assertIn("<FROZEN_INPUT_ARTIFACT", prompt)
        self.assertIn("<LOCAL_OUTPUT_JSON_SCHEMA>", prompt)
        self.assertIn(self.artifact().sha256, prompt)

    def test_default_xhigh_output_budget_matches_mature_m9_analysis_floor(self) -> None:
        payload = build_cross_topic_payload(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
            thinking_type="adaptive",
            effort="xhigh",
        )

        self.assertEqual(payload["max_tokens"], 65536)

    def test_formal_request_binds_distinct_text_and_content_hashes(self) -> None:
        request = self.formal_request()
        payload = build_cross_topic_payload(
            request=request,
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
        )
        prompt = payload["messages"][0]["content"]
        self.assertIn(f'text_sha256="{request.sha256}"', prompt)
        self.assertIn(f'content_sha256="{request.content_sha256}"', prompt)

        output = self.topic_output()
        output["execution"] = dict(output["execution"])
        output["execution"]["request_content_sha256"] = request.content_sha256
        markdown = render_cross_topic_markdown(
            result=self.topic_result(output),
            request=request,
        )
        self.assertIn(f"请求内容 SHA-256：`{request.content_sha256}`", markdown)

    def test_frozen_semantic_contract_is_in_the_single_user_message(self) -> None:
        semantic_contract = {
            "section_order": [
                "scope_and_evidence_boundary",
                "comparison_dimensions",
            ],
            "item_counts": [1, 4],
        }
        payload = build_cross_topic_payload(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
            semantic_contract=semantic_contract,
        )

        prompt = payload["messages"][0]["content"]
        self.assertIn("<FROZEN_SEMANTIC_CONTRACT>", prompt)
        self.assertIn('"item_counts":[1,4]', prompt)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertNotIn("system", payload)

    def test_anthropic_adapter_receives_mature_minimal_request_shape(self) -> None:
        from memorive.models.gateway.providers.anthropic import AnthropicAdapter

        payload = build_cross_topic_payload(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
            max_tokens=2048,
            timeout_seconds=120,
        )
        captured: dict = {}
        output_text = json.dumps(
            {"probe_id": "transport", "status": "ok"}, ensure_ascii=False
        )
        events = (
            {
                "type": "message_start",
                "message": {
                    "id": "msg_probe",
                    "model": "claude-sonnet-5",
                    "usage": {"input_tokens": 10},
                    "content": [],
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": output_text},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
            {"type": "message_stop"},
        )

        class FakeResponse:
            def __iter__(self):
                return iter(
                    [f"data: {json.dumps(event)}\n".encode("utf-8") for event in events]
                )

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        slot = SimpleNamespace(
            task_type="analysis",
            endpoint="https://api.anthropic.test/v1/messages",
            model_id="claude-sonnet-5",
            api_key_env="MEMORIVE_TEST_ANTHROPIC_KEY",
            thinking_type="adaptive",
            effort="xhigh",
            max_tokens=65536,
        )
        with patch.dict(os.environ, {"MEMORIVE_TEST_ANTHROPIC_KEY": "test-only"}), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            response = AnthropicAdapter().invoke(slot, payload)

        body = captured["body"]
        self.assertEqual(body["model"], "claude-sonnet-5")
        self.assertEqual(body["max_tokens"], 2048)
        self.assertEqual(body["thinking"], {"type": "adaptive"})
        self.assertEqual(body["output_config"], {"effort": "xhigh"})
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertNotIn("system", body)
        self.assertNotIn("format", body["output_config"])
        self.assertEqual(response["text"], output_text)
        self.assertTrue(response["transport"]["complete"])

    def test_valid_response_is_parsed_and_locally_validated_once(self) -> None:
        calls: list[dict] = []

        def model_call(payload: dict) -> dict:
            calls.append(payload)
            return self.response()

        result = run_cross_topic_analysis(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
            model_call=model_call,
            max_tokens=2048,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.output, {"probe_id": "transport", "status": "ok"})
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.model, "claude-sonnet-5")
        self.assertRegex(result.prompt_sha256, r"^[A-F0-9]{64}$")
        self.assertRegex(result.response_sha256, r"^[A-F0-9]{64}$")

    def test_single_outer_json_fence_is_accepted_but_surrounding_text_is_not(self) -> None:
        fenced = self.response()
        fenced["text"] = (
            "```json\n"
            '{"probe_id":"transport","status":"ok"}'
            "\n```"
        )
        result = run_cross_topic_analysis(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=OUTPUT_SCHEMA,
            model_call=lambda _payload: fenced,
        )
        self.assertEqual(result.output, {"probe_id": "transport", "status": "ok"})

        narrated = self.response()
        narrated["text"] = "result:\n" + fenced["text"]
        with self.assertRaisesRegex(CrossTopicRunnerError, "MODEL_OUTPUT_INVALID_JSON"):
            run_cross_topic_analysis(
                request=self.request(),
                artifacts=(self.artifact(),),
                output_schema=OUTPUT_SCHEMA,
                model_call=lambda _payload: narrated,
            )

    def test_unbound_default_live_call_is_forbidden(self) -> None:
        with self.assertRaisesRegex(
            CrossTopicRunnerError, "STRICT_MODEL_CALL_BINDING_REQUIRED"
        ):
            run_cross_topic_analysis(
                request=self.request(),
                artifacts=(self.artifact(),),
                output_schema=OUTPUT_SCHEMA,
            )

    def test_artifact_hash_mismatch_fails_before_model_call(self) -> None:
        calls = 0
        artifact = self.artifact()
        invalid = FrozenTextArtifact(
            artifact_id=artifact.artifact_id,
            paper_id=artifact.paper_id,
            artifact_type=artifact.artifact_type,
            evidence_depth=artifact.evidence_depth,
            text=artifact.text + " drift",
            sha256=artifact.sha256,
        )

        def model_call(_payload: dict) -> dict:
            nonlocal calls
            calls += 1
            return self.response()

        with self.assertRaisesRegex(CrossTopicRunnerError, "ARTIFACT_HASH_MISMATCH"):
            run_cross_topic_analysis(
                request=self.request(),
                artifacts=(invalid,),
                output_schema=OUTPUT_SCHEMA,
                model_call=model_call,
            )
        self.assertEqual(calls, 0)

    def test_non_self_remote_input_fails_before_model_call(self) -> None:
        request = self.request()
        blocked = FrozenTopicRequest(
            request_id=request.request_id,
            template_id=request.template_id,
            template_revision=request.template_revision,
            text=request.text,
            sha256=request.sha256,
            data_ownership="entrusted",
        )
        calls = 0

        def model_call(_payload: dict) -> dict:
            nonlocal calls
            calls += 1
            return self.response()

        with self.assertRaisesRegex(CrossTopicRunnerError, "REMOTE_DATA_OWNERSHIP_BLOCKED"):
            run_cross_topic_analysis(
                request=blocked,
                artifacts=(self.artifact(),),
                output_schema=OUTPUT_SCHEMA,
                model_call=model_call,
            )
        self.assertEqual(calls, 0)

    def test_extra_json_or_schema_failure_is_rejected_without_retry(self) -> None:
        for text, expected in (
            ('{"probe_id":"transport","status":"ok"}{"extra":1}', "MODEL_OUTPUT_INVALID_JSON"),
            ('{"probe_id":"transport","status":"bad"}', "MODEL_OUTPUT_SCHEMA_INVALID"),
        ):
            with self.subTest(text=text):
                calls = 0

                def model_call(_payload: dict, value: str = text) -> dict:
                    nonlocal calls
                    calls += 1
                    response = self.response()
                    response["text"] = value
                    return response

                with self.assertRaisesRegex(CrossTopicRunnerError, expected):
                    run_cross_topic_analysis(
                        request=self.request(),
                        artifacts=(self.artifact(),),
                        output_schema=OUTPUT_SCHEMA,
                        model_call=model_call,
                    )
                self.assertEqual(calls, 1)

    def test_returned_model_mismatch_and_incomplete_stream_are_rejected(self) -> None:
        cases = (
            ({"model": "claude-sonnet-4"}, "RETURNED_MODEL_MISMATCH"),
            ({"transport": {"stream": True, "complete": False}}, "STREAM_INCOMPLETE"),
        )
        for changes, expected in cases:
            with self.subTest(changes=changes):
                response = self.response()
                response.update(changes)
                with self.assertRaisesRegex(CrossTopicRunnerError, expected):
                    run_cross_topic_analysis(
                        request=self.request(),
                        artifacts=(self.artifact(),),
                        output_schema=OUTPUT_SCHEMA,
                        model_call=lambda _payload, value=response: value,
                    )

    def test_authoritative_execution_binding_must_match_exactly(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["probe_id", "status", "execution"],
            "properties": {
                "probe_id": {"type": "string"},
                "status": {"const": "ok"},
                "execution": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["call_id"],
                    "properties": {"call_id": {"type": "string"}},
                },
            },
        }
        response = self.response(
            {"probe_id": "binding", "status": "ok", "execution": {"call_id": "wrong"}}
        )
        with self.assertRaisesRegex(CrossTopicRunnerError, "EXECUTION_BINDING_MISMATCH"):
            run_cross_topic_analysis(
                request=self.request(),
                artifacts=(self.artifact(),),
                output_schema=schema,
                expected_execution={"call_id": "expected"},
                model_call=lambda _payload: response,
            )

    def test_authoritative_execution_binding_is_in_the_single_user_message(self) -> None:
        expected_execution = {"call_id": "expected-call-001"}
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["probe_id", "status", "execution"],
            "properties": {
                "probe_id": {"type": "string"},
                "status": {"const": "ok"},
                "execution": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["call_id"],
                    "properties": {"call_id": {"type": "string"}},
                },
            },
        }
        captured: list[dict] = []
        response = self.response(
            {
                "probe_id": "binding",
                "status": "ok",
                "execution": expected_execution,
            }
        )

        run_cross_topic_analysis(
            request=self.request(),
            artifacts=(self.artifact(),),
            output_schema=schema,
            expected_execution=expected_execution,
            model_call=lambda payload: captured.append(payload) or response,
        )

        self.assertEqual(len(captured), 1)
        prompt = captured[0]["messages"][0]["content"]
        self.assertIn("<AUTHORITATIVE_EXECUTION_BINDING>", prompt)
        self.assertIn('"call_id":"expected-call-001"', prompt)
        self.assertNotIn("system", captured[0])

    def test_identity_normalization_is_limited_to_frozen_request_labels(self) -> None:
        expected = self.topic_output()
        observed = deepcopy(expected)
        observed["artifact_id"] = "MODEL-DERIVED-OUTPUT-ID"
        observed["primary_question"] = "model-compressed question"
        for paper in observed["evidence_scope"]["papers"]:
            paper["version_note"] += " model-added explanation"

        normalized, changes = normalize_cross_topic_identity_metadata(
            observed,
            expected_artifact_id=expected["artifact_id"],
            expected_primary_question=expected["primary_question"],
            expected_papers=expected["evidence_scope"]["papers"],
        )

        self.assertEqual(normalized, expected)
        self.assertEqual(observed["artifact_id"], "MODEL-DERIVED-OUTPUT-ID")
        self.assertEqual(len(changes), 6)
        self.assertEqual(
            {change["path"] for change in changes},
            {
                "/artifact_id",
                "/primary_question",
                "/evidence_scope/papers/0/version_note",
                "/evidence_scope/papers/1/version_note",
                "/evidence_scope/papers/2/version_note",
                "/evidence_scope/papers/3/version_note",
            },
        )

        non_normalizable = deepcopy(observed)
        non_normalizable["evidence_scope"]["papers"][0]["card_sha256"] = "F" * 64
        with self.assertRaisesRegex(
            CrossTopicRunnerError, "OUTPUT_IDENTITY_NON_NORMALIZABLE"
        ):
            normalize_cross_topic_identity_metadata(
                non_normalizable,
                expected_artifact_id=expected["artifact_id"],
                expected_primary_question=expected["primary_question"],
                expected_papers=expected["evidence_scope"]["papers"],
            )

    def test_identity_normalization_rejects_missing_expected_question(self) -> None:
        expected = self.topic_output()
        with self.assertRaisesRegex(
            CrossTopicRunnerError, "OUTPUT_IDENTITY_NON_NORMALIZABLE"
        ):
            normalize_cross_topic_identity_metadata(
                expected,
                expected_artifact_id=expected["artifact_id"],
                expected_primary_question="",
                expected_papers=expected["evidence_scope"]["papers"],
            )

    def test_validated_result_renders_deterministic_topic_markdown_bundle(self) -> None:
        result = self.topic_result()
        markdown = render_cross_topic_markdown(
            result=result,
            request=self.request(),
        )
        bundle = render_cross_topic_artifacts(
            result=result,
            request=self.request(),
        )

        self.assertTrue(markdown.startswith("# G1 四篇论文专题分析\n"))
        self.assertIn("分析模型：`anthropic/claude-sonnet-5`", markdown)
        self.assertIn("分析日期：`2026-08-09T20:00:00+09:00`", markdown)
        self.assertIn(
            "专题请求：`Memorive-RESEARCH-HANDOFF-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST@r1.0`",
            markdown,
        )
        self.assertIn("## 核心问题", markdown)
        for index in range(1, 9):
            self.assertIn(f"## 专题第 {index} 节", markdown)
        self.assertEqual(bundle.markdown_text, markdown)
        self.assertEqual(bundle.markdown_sha256, _sha256_text(markdown))
        self.assertEqual(bundle.json_sha256, _sha256_text(bundle.json_text))
        self.assertEqual(json.loads(bundle.json_text), result.output)
        self.assertEqual(
            render_cross_topic_artifacts(result=result, request=self.request()),
            bundle,
        )

    def test_renderer_fails_closed_on_request_or_model_binding_drift(self) -> None:
        output = self.topic_output()
        output["execution"] = dict(output["execution"])
        output["execution"]["request_content_sha256"] = "F" * 64
        with self.assertRaisesRegex(
            CrossTopicRunnerError, "RENDER_REQUEST_BINDING_MISMATCH"
        ):
            render_cross_topic_markdown(
                result=self.topic_result(output),
                request=self.request(),
            )

        result = self.topic_result()
        drifted = CrossTopicRunResult(
            **{**result.__dict__, "model": "claude-sonnet-4"}
        )
        with self.assertRaisesRegex(
            CrossTopicRunnerError, "RENDER_MODEL_BINDING_MISMATCH"
        ):
            render_cross_topic_markdown(result=drifted, request=self.request())


if __name__ == "__main__":
    unittest.main(verbosity=2)
