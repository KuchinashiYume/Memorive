"""PR-OS P03/T08/M04 · cross-topic thin API runner.

Purpose:
    Bind one frozen custom-topic request and its frozen Card/Analysis text
    artifacts to the mature M9 ``analysis`` transport, then perform strict
    local JSON and Draft 2020-12 Schema validation.

Boundary:
    This module does not implement provider HTTP, credentials, retries,
    fallback, accounting, Card generation, or the Phase 1 single-paper
    four-section Analysis parser.  Provider transport remains owned by M9.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import jsonschema


TEMPLATE_ID = "PR-OS-P03-T08-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST"
TEMPLATE_REVISION = "r1.0"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_THINKING_TYPE = "adaptive"
DEFAULT_EFFORT = "xhigh"
# Mature M9 reserves 64 Ki tokens for adaptive/xhigh because reasoning and the
# final JSON share this single Anthropic output ceiling.
DEFAULT_MAX_TOKENS = 65536
DEFAULT_TIMEOUT_SECONDS = 1800

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SHA256 = re.compile(r"^[A-F0-9]{64}$")
_UNRESOLVED_TEMPLATE_TOKEN = re.compile(r"\{\{[^{}]+\}\}")
_ARTIFACT_TYPES = frozenset({"CARD", "ANALYSIS"})
_EVIDENCE_DEPTHS = frozenset({"CARD_ONLY", "CARD_PLUS_SINGLE_ANALYSIS"})
_SUCCESS_STOP_REASONS = frozenset({"end_turn", "stop"})
_TOPIC_SECTION_ORDER = (
    "scope_and_evidence_boundary",
    "comparison_dimensions",
    "evolution_path",
    "consensus_and_continuities",
    "differences_and_tensions",
    "mechanisms_and_tradeoffs",
    "limitations_and_uncertainties",
    "recheck_recommendations",
)


class CrossTopicRunnerError(RuntimeError):
    """Fail-closed P03 topic-runner error; no automatic retry is performed."""


@dataclass(frozen=True)
class FrozenTopicRequest:
    """One already-rendered and hash-frozen P03 topic request."""

    request_id: str
    template_id: str
    template_revision: str
    text: str
    sha256: str
    data_ownership: str
    content_sha256: str | None = None


@dataclass(frozen=True)
class FrozenTextArtifact:
    """One provider-visible, hash-frozen Card or single-paper Analysis."""

    artifact_id: str
    paper_id: str
    artifact_type: str
    evidence_depth: str
    text: str
    sha256: str


@dataclass(frozen=True)
class CrossTopicRunResult:
    """Validated topic-analysis result plus non-secret provider metadata."""

    request_id: str
    output: dict[str, Any]
    raw_text: str
    provider: str
    model: str
    stop_reason: str
    transport: dict[str, Any]
    usage: dict[str, Any]
    prompt_sha256: str
    response_sha256: str


@dataclass(frozen=True)
class CrossTopicRenderedArtifacts:
    """Deterministic, caller-writable JSON and Markdown report artifacts."""

    request_id: str
    artifact_id: str
    json_text: str
    markdown_text: str
    json_sha256: str
    markdown_sha256: str


ModelCall = Callable[[dict[str, Any]], Mapping[str, Any]]
RegionCheck = Callable[[], Any]
SemanticValidator = Callable[[dict[str, Any]], Any]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _require_safe_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise CrossTopicRunnerError(f"{label}_INVALID")


def normalize_cross_topic_identity_metadata(
    output: Mapping[str, Any],
    *,
    expected_artifact_id: str,
    expected_primary_question: str,
    expected_papers: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize only runtime-owned identity labels after strict comparison.

    ``artifact_id``, the frozen request's verbatim ``primary_question``, and
    each paper's human-readable ``version_note`` are runtime-owned labels.
    Every other paper identity field must already match the frozen binding
    exactly; otherwise normalization fails closed.  The caller receives an
    explicit change list for its evidence receipt.
    """

    if not isinstance(output, Mapping):
        raise CrossTopicRunnerError("OUTPUT_IDENTITY_NON_NORMALIZABLE:OUTPUT_TYPE")
    _require_safe_id(expected_artifact_id, "EXPECTED_ARTIFACT_ID")
    if not isinstance(expected_primary_question, str) or not expected_primary_question:
        raise CrossTopicRunnerError(
            "OUTPUT_IDENTITY_NON_NORMALIZABLE:PRIMARY_QUESTION"
        )
    if not isinstance(expected_papers, Sequence) or isinstance(
        expected_papers, (str, bytes)
    ):
        raise CrossTopicRunnerError("OUTPUT_IDENTITY_NON_NORMALIZABLE:PAPERS_TYPE")

    normalized = deepcopy(dict(output))
    scope = normalized.get("evidence_scope")
    observed_papers = scope.get("papers") if isinstance(scope, Mapping) else None
    if not isinstance(observed_papers, list) or len(observed_papers) != len(
        expected_papers
    ):
        raise CrossTopicRunnerError("OUTPUT_IDENTITY_NON_NORMALIZABLE:PAPER_COUNT")

    changes: list[dict[str, Any]] = []
    observed_artifact_id = normalized.get("artifact_id")
    if observed_artifact_id != expected_artifact_id:
        changes.append(
            {
                "path": "/artifact_id",
                "observed": observed_artifact_id,
                "normalized": expected_artifact_id,
            }
        )
        normalized["artifact_id"] = expected_artifact_id

    observed_primary_question = normalized.get("primary_question")
    if observed_primary_question != expected_primary_question:
        changes.append(
            {
                "path": "/primary_question",
                "observed": observed_primary_question,
                "normalized": expected_primary_question,
            }
        )
        normalized["primary_question"] = expected_primary_question

    for index, (observed, expected_source) in enumerate(
        zip(observed_papers, expected_papers, strict=True)
    ):
        if not isinstance(observed, Mapping) or not isinstance(
            expected_source, Mapping
        ):
            raise CrossTopicRunnerError(
                "OUTPUT_IDENTITY_NON_NORMALIZABLE:PAPER_OBJECT"
            )
        expected = dict(expected_source)
        if set(observed) != set(expected) or "version_note" not in expected:
            raise CrossTopicRunnerError(
                "OUTPUT_IDENTITY_NON_NORMALIZABLE:PAPER_SHAPE"
            )
        for key, expected_value in expected.items():
            if key != "version_note" and observed.get(key) != expected_value:
                raise CrossTopicRunnerError(
                    f"OUTPUT_IDENTITY_NON_NORMALIZABLE:{index}:{key}"
                )
        if observed.get("version_note") != expected["version_note"]:
            changes.append(
                {
                    "path": f"/evidence_scope/papers/{index}/version_note",
                    "observed": observed.get("version_note"),
                    "normalized": expected["version_note"],
                }
            )
            observed["version_note"] = expected["version_note"]

    return normalized, changes


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CrossTopicRunnerError(f"{label}_SHA256_INVALID")


def _request_content_sha256(request: FrozenTopicRequest) -> str:
    return request.content_sha256 or request.sha256


def _validate_request(request: FrozenTopicRequest) -> None:
    if not isinstance(request, FrozenTopicRequest):
        raise CrossTopicRunnerError("REQUEST_TYPE_INVALID")
    _require_safe_id(request.request_id, "REQUEST_ID")
    if request.template_id != TEMPLATE_ID:
        raise CrossTopicRunnerError("REQUEST_TEMPLATE_ID_MISMATCH")
    if request.template_revision != TEMPLATE_REVISION:
        raise CrossTopicRunnerError("REQUEST_TEMPLATE_REVISION_MISMATCH")
    if request.data_ownership != "self":
        raise CrossTopicRunnerError("REMOTE_DATA_OWNERSHIP_BLOCKED")
    if not isinstance(request.text, str) or not request.text.strip():
        raise CrossTopicRunnerError("REQUEST_TEXT_EMPTY")
    _require_hash(request.sha256, "REQUEST")
    if _sha256_text(request.text) != request.sha256:
        raise CrossTopicRunnerError("REQUEST_HASH_MISMATCH")
    content_sha256 = _request_content_sha256(request)
    _require_hash(content_sha256, "REQUEST_CONTENT")
    if request.content_sha256 is not None and content_sha256 not in request.text:
        raise CrossTopicRunnerError("REQUEST_CONTENT_HASH_NOT_RENDERED")
    if TEMPLATE_ID not in request.text or TEMPLATE_REVISION not in request.text:
        raise CrossTopicRunnerError("REQUEST_TEMPLATE_IDENTITY_NOT_RENDERED")
    if request.request_id not in request.text:
        raise CrossTopicRunnerError("REQUEST_ID_NOT_RENDERED")
    if _UNRESOLVED_TEMPLATE_TOKEN.search(request.text):
        raise CrossTopicRunnerError("REQUEST_TEMPLATE_TOKEN_UNRESOLVED")
    if "</FORMAL_TOPIC_REQUEST>" in request.text:
        raise CrossTopicRunnerError("REQUEST_BOUNDARY_TOKEN_COLLISION")


def _validate_artifacts(artifacts: Sequence[FrozenTextArtifact]) -> None:
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        raise CrossTopicRunnerError("ARTIFACTS_TYPE_INVALID")
    if not artifacts:
        raise CrossTopicRunnerError("ARTIFACTS_EMPTY")
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, FrozenTextArtifact):
            raise CrossTopicRunnerError("ARTIFACT_TYPE_INVALID")
        _require_safe_id(artifact.artifact_id, "ARTIFACT_ID")
        _require_safe_id(artifact.paper_id, "PAPER_ID")
        if artifact.artifact_id in seen:
            raise CrossTopicRunnerError("ARTIFACT_ID_DUPLICATE")
        seen.add(artifact.artifact_id)
        if artifact.artifact_type not in _ARTIFACT_TYPES:
            raise CrossTopicRunnerError("ARTIFACT_KIND_INVALID")
        if artifact.evidence_depth not in _EVIDENCE_DEPTHS:
            raise CrossTopicRunnerError("EVIDENCE_DEPTH_INVALID")
        if artifact.artifact_type == "ANALYSIS" and artifact.evidence_depth == "CARD_ONLY":
            raise CrossTopicRunnerError("CARD_ONLY_ANALYSIS_FORBIDDEN")
        if not isinstance(artifact.text, str) or not artifact.text.strip():
            raise CrossTopicRunnerError("ARTIFACT_TEXT_EMPTY")
        _require_hash(artifact.sha256, "ARTIFACT")
        if _sha256_text(artifact.text) != artifact.sha256:
            raise CrossTopicRunnerError("ARTIFACT_HASH_MISMATCH")
        if "</FROZEN_INPUT_ARTIFACT>" in artifact.text:
            raise CrossTopicRunnerError("ARTIFACT_BOUNDARY_TOKEN_COLLISION")


def _validate_output_schema(output_schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(output_schema, Mapping):
        raise CrossTopicRunnerError("OUTPUT_SCHEMA_TYPE_INVALID")
    schema = dict(output_schema)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise CrossTopicRunnerError("OUTPUT_SCHEMA_INVALID") from exc
    return schema


def _build_prompt(
    request: FrozenTopicRequest,
    artifacts: Sequence[FrozenTextArtifact],
    output_schema: Mapping[str, Any],
    execution_binding: Mapping[str, Any] | None,
    semantic_contract: Mapping[str, Any] | None,
) -> str:
    schema_text = json.dumps(
        output_schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parts = [
        "你正在执行 PR-OS P03/T08 的冻结专题分析任务。",
        "仅使用下方冻结任务单和冻结文本资产；不得使用外部来源或补写缺失的单篇 Analysis。",
        "只返回一个符合 LOCAL_OUTPUT_JSON_SCHEMA 的 JSON object；不要 Markdown fence、额外解释或隐藏思维链。",
        "Schema 只是本地验收合同；不要复制或输出 Schema 本身。",
    ]
    if execution_binding is not None:
        execution_text = json.dumps(
            dict(execution_binding),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if "</AUTHORITATIVE_EXECUTION_BINDING>" in execution_text:
            raise CrossTopicRunnerError("EXECUTION_BINDING_BOUNDARY_TOKEN_COLLISION")
        parts.extend(
            [
                "逐字复制 AUTHORITATIVE_EXECUTION_BINDING 到输出的 execution 字段；不得推断或改写。",
                "<AUTHORITATIVE_EXECUTION_BINDING>",
                execution_text,
                "</AUTHORITATIVE_EXECUTION_BINDING>",
            ]
        )
    if semantic_contract is not None:
        semantic_text = json.dumps(
            dict(semantic_contract),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if "</FROZEN_SEMANTIC_CONTRACT>" in semantic_text:
            raise CrossTopicRunnerError("SEMANTIC_CONTRACT_BOUNDARY_TOKEN_COLLISION")
        parts.extend(
            [
                "严格满足 FROZEN_SEMANTIC_CONTRACT；该合同只约束结构、覆盖和证据，不是新增事实来源。",
                "<FROZEN_SEMANTIC_CONTRACT>",
                semantic_text,
                "</FROZEN_SEMANTIC_CONTRACT>",
            ]
        )
    parts.extend(
        [
            (
            f'<FORMAL_TOPIC_REQUEST request_id="{request.request_id}" '
            f'template_id="{request.template_id}" '
            f'revision="{request.template_revision}" '
            f'text_sha256="{request.sha256}" '
            f'content_sha256="{_request_content_sha256(request)}">'
            ),
            request.text.rstrip(),
            "</FORMAL_TOPIC_REQUEST>",
            "<FROZEN_INPUT_ARTIFACTS>",
        ]
    )
    for artifact in artifacts:
        parts.extend(
            [
                (
                    f'<FROZEN_INPUT_ARTIFACT artifact_id="{artifact.artifact_id}" '
                    f'paper_id="{artifact.paper_id}" type="{artifact.artifact_type}" '
                    f'evidence_depth="{artifact.evidence_depth}" sha256="{artifact.sha256}">'
                ),
                artifact.text.rstrip(),
                "</FROZEN_INPUT_ARTIFACT>",
            ]
        )
    parts.extend(
        [
            "</FROZEN_INPUT_ARTIFACTS>",
            "<LOCAL_OUTPUT_JSON_SCHEMA>",
            schema_text,
            "</LOCAL_OUTPUT_JSON_SCHEMA>",
        ]
    )
    return "\n\n".join(parts).rstrip() + "\n"


def build_cross_topic_payload(
    *,
    request: FrozenTopicRequest,
    artifacts: Sequence[FrozenTextArtifact],
    output_schema: Mapping[str, Any],
    thinking_type: str = DEFAULT_THINKING_TYPE,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_seconds: int | float = DEFAULT_TIMEOUT_SECONDS,
    execution_binding: Mapping[str, Any] | None = None,
    semantic_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the mature M9 user-message payload without provider-native Schema."""

    _validate_request(request)
    _validate_artifacts(artifacts)
    schema = _validate_output_schema(output_schema)
    if thinking_type not in {"adaptive", "enabled", "disabled"}:
        raise CrossTopicRunnerError("THINKING_TYPE_INVALID")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise CrossTopicRunnerError("EFFORT_INVALID")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise CrossTopicRunnerError("MAX_TOKENS_INVALID")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise CrossTopicRunnerError("TIMEOUT_INVALID")
    if execution_binding is not None and not isinstance(execution_binding, Mapping):
        raise CrossTopicRunnerError("EXECUTION_BINDING_TYPE_INVALID")
    if semantic_contract is not None and not isinstance(semantic_contract, Mapping):
        raise CrossTopicRunnerError("SEMANTIC_CONTRACT_TYPE_INVALID")
    prompt = _build_prompt(
        request,
        artifacts,
        schema,
        execution_binding,
        semantic_contract,
    )
    return {
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": thinking_type},
        "effort": effort,
        "max_tokens": max_tokens,
        "stream": True,
        "timeout": timeout_seconds,
    }


def _parse_output(text: Any) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise CrossTopicRunnerError("MODEL_OUTPUT_EMPTY")
    candidate = text.strip()
    fenced = re.fullmatch(
        r"```json[ \t]*\r?\n(?P<body>.*)\r?\n```",
        candidate,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced is not None:
        candidate = fenced.group("body")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise CrossTopicRunnerError("MODEL_OUTPUT_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise CrossTopicRunnerError("MODEL_OUTPUT_NOT_OBJECT")
    return value


def run_cross_topic_analysis(
    *,
    request: FrozenTopicRequest,
    artifacts: Sequence[FrozenTextArtifact],
    output_schema: Mapping[str, Any],
    expected_provider: str = DEFAULT_PROVIDER,
    expected_model: str = DEFAULT_MODEL,
    thinking_type: str = DEFAULT_THINKING_TYPE,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_seconds: int | float = DEFAULT_TIMEOUT_SECONDS,
    expected_execution: Mapping[str, Any] | None = None,
    semantic_contract: Mapping[str, Any] | None = None,
    semantic_validator: SemanticValidator | None = None,
    model_call: ModelCall | None = None,
    region_check: RegionCheck | None = None,
) -> CrossTopicRunResult:
    """Run exactly one cross-topic model request and validate it locally.

    The function itself never retries.  A caller that needs a zero-retry run
    must also configure the shared M9 fallback policy accordingly before the
    call.  Injected ``model_call`` is the test/run seam used by governed
    harnesses with run-local profiles and strict call-evidence writers.
    """

    _require_safe_id(expected_provider, "EXPECTED_PROVIDER")
    _require_safe_id(expected_model, "EXPECTED_MODEL")
    payload = build_cross_topic_payload(
        request=request,
        artifacts=artifacts,
        output_schema=output_schema,
        thinking_type=thinking_type,
        effort=effort,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        execution_binding=expected_execution,
        semantic_contract=semantic_contract,
    )
    if model_call is None:
        raise CrossTopicRunnerError("STRICT_MODEL_CALL_BINDING_REQUIRED")
    if region_check is not None:
        region_check()
    response = model_call(payload)
    if not isinstance(response, Mapping):
        raise CrossTopicRunnerError("MODEL_RESPONSE_TYPE_INVALID")
    provider = response.get("provider")
    model = response.get("model")
    if provider != expected_provider:
        raise CrossTopicRunnerError("RETURNED_PROVIDER_MISMATCH")
    if model != expected_model:
        raise CrossTopicRunnerError("RETURNED_MODEL_MISMATCH")
    stop_reason = response.get("stop_reason")
    if stop_reason not in _SUCCESS_STOP_REASONS:
        raise CrossTopicRunnerError("MODEL_STOP_REASON_NOT_COMPLETE")
    transport = response.get("transport")
    if not isinstance(transport, Mapping) or transport.get("complete") is not True:
        raise CrossTopicRunnerError("STREAM_INCOMPLETE")
    if transport.get("stream") is not True:
        raise CrossTopicRunnerError("STREAM_MODE_MISMATCH")
    raw_text = response.get("text")
    output = _parse_output(raw_text)
    schema = _validate_output_schema(output_schema)
    try:
        jsonschema.Draft202012Validator(schema).validate(output)
    except jsonschema.ValidationError as exc:
        raise CrossTopicRunnerError("MODEL_OUTPUT_SCHEMA_INVALID") from exc
    if expected_execution is not None:
        expected = dict(expected_execution)
        if output.get("execution") != expected:
            raise CrossTopicRunnerError("EXECUTION_BINDING_MISMATCH")
    if semantic_validator is not None:
        try:
            semantic_validator(output)
        except CrossTopicRunnerError:
            raise
        except Exception as exc:
            raise CrossTopicRunnerError("SEMANTIC_VALIDATION_FAILED") from exc
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise CrossTopicRunnerError("USAGE_MISSING")
    prompt = payload["messages"][0]["content"]
    return CrossTopicRunResult(
        request_id=request.request_id,
        output=output,
        raw_text=raw_text,
        provider=provider,
        model=model,
        stop_reason=stop_reason,
        transport=dict(transport),
        usage=dict(usage),
        prompt_sha256=_sha256_text(prompt),
        response_sha256=_sha256_text(raw_text),
    )


def _validate_render_bindings(
    *, result: CrossTopicRunResult, request: FrozenTopicRequest
) -> dict[str, Any]:
    _validate_request(request)
    if not isinstance(result, CrossTopicRunResult):
        raise CrossTopicRunnerError("RENDER_RESULT_TYPE_INVALID")
    if result.request_id != request.request_id:
        raise CrossTopicRunnerError("RENDER_REQUEST_BINDING_MISMATCH")
    if not isinstance(result.output, dict):
        raise CrossTopicRunnerError("RENDER_OUTPUT_TYPE_INVALID")
    if not isinstance(result.raw_text, str):
        raise CrossTopicRunnerError("RENDER_RAW_TEXT_INVALID")
    if _sha256_text(result.raw_text) != result.response_sha256:
        raise CrossTopicRunnerError("RENDER_RESPONSE_HASH_MISMATCH")
    try:
        parsed = _parse_output(result.raw_text)
    except CrossTopicRunnerError as exc:
        raise CrossTopicRunnerError("RENDER_RAW_OUTPUT_INVALID") from exc
    if parsed != result.output:
        raise CrossTopicRunnerError("RENDER_OUTPUT_BINDING_MISMATCH")

    execution = result.output.get("execution")
    if not isinstance(execution, dict):
        raise CrossTopicRunnerError("RENDER_EXECUTION_INVALID")
    request_binding = (
        execution.get("request_id"),
        execution.get("request_template_id"),
        execution.get("request_template_revision"),
        execution.get("request_content_sha256"),
    )
    expected_request_binding = (
        request.request_id,
        request.template_id,
        request.template_revision,
        _request_content_sha256(request),
    )
    if request_binding != expected_request_binding:
        raise CrossTopicRunnerError("RENDER_REQUEST_BINDING_MISMATCH")
    if (
        execution.get("provider") != result.provider
        or execution.get("analysis_model") != result.model
    ):
        raise CrossTopicRunnerError("RENDER_MODEL_BINDING_MISMATCH")

    sections = result.output.get("sections")
    if not isinstance(sections, list) or tuple(
        section.get("section_id") if isinstance(section, dict) else None
        for section in sections
    ) != _TOPIC_SECTION_ORDER:
        raise CrossTopicRunnerError("RENDER_SECTION_ORDER_MISMATCH")
    judgment = result.output.get("judgment_boundary")
    if not isinstance(judgment, dict) or (
        judgment.get("winner_selected") is not False
        or judgment.get("overall_scientific_score_assigned") is not False
        or judgment.get("final_user_judgment_made") is not False
        or judgment.get("scientific_maturity") != "NOT_ASSESSED"
    ):
        raise CrossTopicRunnerError("RENDER_JUDGMENT_BOUNDARY_INVALID")
    return execution


def _markdown_table_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _render_evidence_ref(ref: Mapping[str, Any]) -> str:
    return (
        f"`{ref['paper_id']}` / `{ref['artifact_type']}` / `{ref['source_id']}` / "
        f"`{ref['evidence_depth']}` / `{ref['artifact_sha256']}`"
    )


def render_cross_topic_markdown(
    *, result: CrossTopicRunResult, request: FrozenTopicRequest
) -> str:
    """Render one validated custom-topic result as the formal eight-section report.

    The frozen custom-topic request remains the authoritative task template.
    This renderer does not reinterpret that request; it binds its identity to
    the validated output and exposes the output contract as deterministic
    human-readable Markdown.
    """

    execution = _validate_render_bindings(result=result, request=request)
    output = result.output
    try:
        lines = [
            f"# {output['title']}",
            "",
            "## 执行元数据",
            "",
            f"- artifact_id：`{output['artifact_id']}`",
            f"- schema_version：`{output['schema_version']}`",
            f"- 分析模型：`{execution['provider']}/{execution['analysis_model']}`",
            f"- thinking：`{execution['thinking_type']}`",
            f"- effort：`{execution['effort']}`",
            f"- max_tokens：`{execution['max_tokens']}`",
            f"- 分析日期：`{execution['analyzed_at']}`",
            f"- call_id：`{execution['call_id']}`",
            f"- 源提交：`{execution['source_commit']}`",
            f"- 源树 manifest SHA-256：`{execution['source_tree_manifest_sha256']}`",
            (
                f"- 专题请求：`{execution['request_template_id']}@"
                f"{execution['request_template_revision']}` / `{execution['request_id']}`"
            ),
            f"- 请求内容 SHA-256：`{execution['request_content_sha256']}`",
            "",
            "## 核心问题",
            "",
            output["primary_question"],
            "",
            "## 前置回答",
            "",
            output["executive_answer"],
            "",
            "## 证据范围",
            "",
            f"- input_snapshot_id：`{output['evidence_scope']['input_snapshot_id']}`",
            (
                "- input_manifest_sha256："
                f"`{output['evidence_scope']['input_manifest_sha256']}`"
            ),
            "",
            "| paper_id | 实际版本 | 证据深度 | Card SHA-256 | Analysis SHA-256 |",
            "|---|---|---|---|---|",
        ]
        for paper in output["evidence_scope"]["papers"]:
            lines.append(
                f"| {_markdown_table_cell(paper['paper_id'])} | "
                f"{_markdown_table_cell(paper['version_note'])} | "
                f"{_markdown_table_cell(paper['evidence_depth'])} | "
                f"`{paper['card_sha256']}` | `{paper['analysis_sha256']}` |"
            )
        lines.extend(["", "## 必查分面覆盖", ""])
        for facet in output["required_facet_coverage"]:
            lines.append(
                f"- `{facet['facet_id']}`："
                f"{'已覆盖' if facet['covered'] else '未覆盖'}；"
                f"items={', '.join(facet['supporting_item_ids'])}；"
                f"{facet['boundary_note']}"
            )
        for section in output["sections"]:
            lines.extend(
                [
                    "",
                    f"## {section['heading']}",
                    "",
                    f"section_id：`{section['section_id']}`",
                    "",
                    section["summary"],
                    "",
                ]
            )
            for item in section["items"]:
                lines.extend([f"### {item['item_id']}", "", item["statement"], ""])
                lines.extend(["证据：", ""])
                for ref in item["evidence_refs"]:
                    lines.append(f"- {_render_evidence_ref(ref)}")
                lines.extend(
                    ["", "限定：" + "；".join(item["qualifiers"]), ""]
                )
        lines.extend(["## 四篇技术演变顺序", ""])
        for stage in output["paper_trajectory"]:
            lines.append(
                f"{stage['sequence']}. **{stage['paper_id']} — {stage['role']}**："
                f"{stage['transition_from_prior']}"
            )
            lines.append("   - 证据：" + "；".join(
                _render_evidence_ref(ref) for ref in stage["evidence_refs"]
            ))
        lines.extend(
            [
                "",
                "## 判断边界",
                "",
                "- 未选择赢家。",
                "- 未给科研价值总分。",
                "- 未替用户作最终判断。",
                (
                    "- scientific_maturity："
                    f"`{output['judgment_boundary']['scientific_maturity']}`。"
                ),
                "",
            ]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CrossTopicRunnerError("RENDER_INPUT_INVALID") from exc
    return "\n".join(lines)


def render_cross_topic_artifacts(
    *, result: CrossTopicRunResult, request: FrozenTopicRequest
) -> CrossTopicRenderedArtifacts:
    """Return normalized JSON and Markdown for create-only run-local writing."""

    markdown_text = render_cross_topic_markdown(result=result, request=request)
    json_text = json.dumps(
        result.output,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).rstrip() + "\n"
    return CrossTopicRenderedArtifacts(
        request_id=request.request_id,
        artifact_id=str(result.output["artifact_id"]),
        json_text=json_text,
        markdown_text=markdown_text,
        json_sha256=_sha256_text(json_text),
        markdown_sha256=_sha256_text(markdown_text),
    )
