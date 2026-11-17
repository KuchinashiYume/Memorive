"""The model runners.

ModelValidationRunner is the interface the settings controller holds. FailClosedModelValidationRunner
answers every request with NOT_RUN; it is used outside the desktop service. LiveModelValidationRunner
sends the requests: verify_api_model and verify_cli_model check a profile; execute_structured_chat
checks a chat, sends it through _send_api, _send_cli or _send_local and builds the result in
_chat_result; embeddings, OCR and reranking come from its two mixins (embeddings.py,
ocr_and_rerank.py). _ChatAttempt holds one attempt's evidence. validation_result is the result of a
check before it runs; default_executable_resolver finds the command-line tool a profile names.
"""
from __future__ import annotations

from memorive.models.scheduling import scheduled
from memorive.models.calls.ledger import CallLedger, RecordingTransport, recorded_execution
from memorive.models.gateway.prompt_cache import cache_usage
from memorive.models.gateway.cache_workspace import structured_chat_workspace, write_response_schema
from memorive.models.gateway.cache_session import Continuation
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
import unicodedata
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)
from memorive.models.capabilities import infer_api_model_capability
from memorive.models.endpoints import SAFE_MODEL, SAFE_SHA256, _api_profile, json_sha256
from memorive.models.gateway.resource_limits import resolve_call_limits

from memorive.models.validation.api_route import (
    _api_chat_request,
    _api_chat_response,
    _api_chat_url,
    _api_failure_evidence,
)
from memorive.models.validation.catalog import (
    MAX_CATALOG_PAGES,
    _catalog_choices,
    _catalog_document,
    _catalog_match,
    _catalog_model_ids,
    _catalog_suggestion,
    _next_catalog_url,
)
from memorive.models.validation.cli_route import (
    _cli_argv,
    _cli_failure_diagnostic,
    _codex_refinement_transport_schema,
    _codex_transport_schema,
    _execution_codex_event_diagnostic,
    _cli_receipt_route,
    _resolved_file_sha256,
)
from memorive.models.validation.embeddings import _EmbeddingExecutionMixin
from memorive.models.validation.ocr_and_rerank import _SpecializedModelExecutionMixin
from memorive.models.validation.structured import (
    _structured_document_from_output,
    _structured_prompt,
)
from memorive.models.validation.transport import (
    BoundedSubprocessTransport,
    HttpTransport,
    ProcessResult,
    ProcessTransport,
    UrllibHttpTransport,
    _ResponseInterrupted,
)
from memorive.models.validation.usage import (
    _api_estimated_cost_cny,
    _normalise_token_usage,
    _reported_models,
    _usage_from_cli_output,
)


class ModelValidationRunner(Protocol):
    """Explicit validation boundary; saving settings never invokes it."""

    def verify_api_model(self, service: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def verify_cli_model(
        self,
        cli_service: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute_structured_chat(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Mapping[str, Any]: ...

    def execute_embeddings(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        inputs: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]: ...

    def execute_ocr_image(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]: ...

    def execute_rerank(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        query: str,
        documents: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]: ...


class FailClosedModelValidationRunner:
    """Fallback boundary used outside the real desktop service."""

    @staticmethod
    def not_run(kind: str) -> dict[str, Any]:
        return {
            "schema_version": "SettingsModelValidationResult-v1",
            "kind": kind,
            "status": "NOT_RUN",
            "score": None,
            "reason": "MODEL_VALIDATION_RUNNER_NOT_CONFIGURED",
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
        }

    def execute_embeddings(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        inputs: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        del service, model, inputs, purpose, timeout_seconds
        return {
            **self.not_run(profile_kind),
            "schema_version": "SettingsEmbeddingRunnerResult-v1",
            "status": "FAILED",
            "reason": "EMBEDDING_RUNNER_NOT_CONFIGURED",
        }

    def execute_ocr_image(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        del service, model, image_bytes, mime_type, prompt, purpose, max_output_tokens, timeout_seconds
        return {
            **self.not_run(profile_kind),
            "schema_version": "SettingsOcrImageRunnerResult-v1",
            "status": "FAILED",
            "reason": "OCR_IMAGE_RUNNER_NOT_CONFIGURED",
        }

    def execute_rerank(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        query: str,
        documents: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        del service, model, query, documents, purpose, timeout_seconds
        return {
            **self.not_run(profile_kind),
            "schema_version": "SettingsRerankRunnerResult-v1",
            "status": "FAILED",
            "reason": "RERANK_RUNNER_NOT_CONFIGURED",
        }

    def verify_api_model(self, service: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.not_run("API")

    def verify_cli_model(
        self,
        cli_service: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self.not_run("CLI")

    def execute_structured_chat(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        del max_output_tokens, timeout_seconds
        return {
            **self.not_run(profile_kind),
            "schema_version": "SettingsStructuredChatRunnerResult-v1",
            "status": "FAILED",
            "reason": "STRUCTURED_CHAT_RUNNER_NOT_CONFIGURED",
        }


def _structured_chat_timeout_limit(purpose: str) -> int:
    if purpose in {"workflow_model_exam", "core_document_processing"}:
        return 3_600
    return 600


def validation_result(kind: str, *, status: str, reason: str) -> dict[str, Any]:
    """The result of a verification request before it runs (also the start of a workflow exam's result)."""
    return {
        "schema_version": "SettingsModelValidationResult-v1",
        "kind": kind,
        "status": status,
        "score": None,
        "reason": reason,
        "requested_model": None,
        "returned_model": None,
        "duration_ms": 0,
        "external_network_calls": 0,
        "provider_calls": 0,
        "external_model_calls": 0,
        "external_process_launches": 0,
    }


def default_executable_resolver(value: str) -> str | None:
    """The path of the command-line tool a profile names, or None (also used by the workflow exams)."""
    if not isinstance(value, str) or not value or len(value) > 260:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        if os.name == "nt" and candidate.name.lower() in {"codex.cmd", "codex.bat"}:
            from memorive.models.cli.windows import native_codex_from_shim
            return native_codex_from_shim(candidate)
        return str(candidate.resolve()) if candidate.is_file() else None
    if any(separator in value for separator in ("/", "\\")):
        return None
    if os.name == "nt":
        from memorive.models.cli.windows import resolve_supported_cli_executable
        supported_cli = resolve_supported_cli_executable(value)
        if supported_cli:
            return supported_cli
    located = shutil.which(value)
    if os.name == "nt" and located and Path(located).name.lower() in {"codex.cmd", "codex.bat"}:
        from memorive.models.cli.windows import native_codex_from_shim
        return native_codex_from_shim(located)
    return str(Path(located).resolve()) if located else None


@dataclass
class _ChatAttempt:
    """One structured chat request while it runs (LiveModelValidationRunner.execute_structured_chat).

    The request and what preparation decided are set when it is made; the route that sends it
    (_send_api, _send_cli, _send_local) fills in the reply, the counts and the route's evidence;
    the finish reads them. failed() builds the result of a request that did not succeed, from the
    values as they are at that moment."""

    # The request.
    profile_kind: str
    service: Mapping[str, Any]
    model: Mapping[str, Any]
    prompt: str
    response_schema: Mapping[str, Any]
    purpose: str
    timeout_seconds: int | None
    # Decided by preparation; the API route may set limits and effective_max_output_tokens again,
    # the CLI route behavior_hash.
    requested_model: str
    output_limit_mode: str | None
    limits: Mapping[str, Any]
    effective_max_output_tokens: int | None
    prompt_bytes: bytes
    behavior_hash: str
    started_at: float
    strict_cli_identity_binding: bool
    # Filled in by the route that sends it.
    response: Mapping[str, Any] | None = None
    returned_model: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    route: str = ''
    region: str = ''
    egress: str = ''
    external_network_calls: int = 0
    provider_calls: int = 0
    external_process_launches: int = 0
    transport_diagnostic: Mapping[str, Any] | None = None
    api_provider_family: str | None = None
    cli_binary_sha256: str | None = None
    cli_version: str | None = None
    cli_identity_authority_ref: str | None = None
    request_model_binding_status: str | None = None
    request_model_binding_sha256: str | None = None
    model_allowlist_sha256: str | None = None

    def failed(
        self,
        reason: str,
        *,
        returned_model: str | None,
        raw_usage: Mapping[str, Any] | None,
        external_network_calls: int,
        provider_calls: int,
        external_model_calls: int,
        external_process_launches: int,
        route: str,
        region: str,
        egress: str,
        transport_diagnostic: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        token_usage = _normalise_token_usage(raw_usage or {})
        estimated_cost_cny = (
            _api_estimated_cost_cny(
                self.requested_model, self.model, token_usage
            )
            if self.profile_kind == "API"
            else (0.0 if self.profile_kind == "LOCAL" else None)
        )
        if self.profile_kind == "CLI":
            cost_evidence = "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
        elif self.profile_kind == "LOCAL":
            cost_evidence = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
        elif estimated_cost_cny is not None:
            cost_evidence = (
                "FROZEN_TARGET_PRICING_PROFILE_ESTIMATE"
                if self.model.get("pricing_evidence_ref")
                else "FROZEN_Quality_PUBLIC_PRICING_PROFILE_2026-07-30_ESTIMATE"
            )
        else:
            cost_evidence = "ATTEMPT_COST_EVIDENCE_UNAVAILABLE"
        duration_ms = max(0, round((time.monotonic() - self.started_at) * 1000))
        execution_receipt = {
            "schema_version": "SettingsStructuredChatExecutionReceipt-v2",
            "status": "FAILED",
            "profile_kind": self.profile_kind,
            "purpose": self.purpose,
            "route": route,
            "requested_model": self.requested_model,
            "returned_model": returned_model,
            "behavior_sha256": self.behavior_hash,
            "response_sha256": None,
            "duration_ms": duration_ms,
            "token_usage": token_usage or None,
            "prompt_cache": cache_usage(dict(raw_usage or {})),
            "actual_cost": None,
            "estimated_cost_cny": estimated_cost_cny,
            "cost_evidence": cost_evidence,
            "region": region,
            "egress": egress,
            "failure_reason": reason,
            "quality_assessment": 'NOT_ASSESSED',
            "resource_limits": self.limits,
            "capacity_assessment": (
                'NOT_ASSESSED' if reason in {'STRUCTURED_CHAT_JSON_TRUNCATED',
                    'MODEL_OUTPUT_TRUNCATED', 'API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN',
                    'CLI_OUTPUT_LIMIT_EXCEEDED', 'CLI_INPUT_TOO_LARGE'} else None
            ),
            "output_budget": {
                "effective_sent_tokens": self.effective_max_output_tokens,
                "source": (
                    "CALLER_VERIFIED_PROFILE"
                    if self.effective_max_output_tokens is not None
                    else (
                        "CLI_PROVIDER_MANAGED"
                        if self.profile_kind == "CLI"
                        else "PROVIDER_OR_LOCAL_RUNTIME_MANAGED"
                    )
                ),
                "product_hard_limit_added": False,
            },
            "max_output_tokens_mode": (
                "PROVIDER_DEFAULT"
                if self.effective_max_output_tokens is None
                else "EXPLICIT"
            ),
            "request_max_output_tokens": self.effective_max_output_tokens,
        }
        if self.purpose == "workflow_model_exam":
            execution_receipt["output_limit_contract"] = (
                workflow_exam_output_limit_contract()
            )
            execution_receipt["length_only_failure_quality_semantics"] = (
                "NOT_ASSESSED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
            )
        if isinstance(transport_diagnostic, Mapping):
            execution_receipt["transport_diagnostic"] = dict(
                transport_diagnostic
            )
        return {
            "schema_version": "SettingsStructuredChatRunnerResult-v1",
            "status": "FAILED",
            "reason": reason,
            "requested_model": self.requested_model,
            "returned_model": returned_model,
            "duration_ms": duration_ms,
            "external_network_calls": external_network_calls,
            "provider_calls": provider_calls,
            "external_model_calls": external_model_calls,
            "external_process_launches": external_process_launches,
            "execution_receipt": execution_receipt,
        }

    def tool_failed(
        self,
        reason: str,
        *,
        returned_model: str | None = None,
        transport_diagnostic: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """A failed result after the command-line tool ran: one model call and one process launch, no
        direct network or provider call, the usage the tool reported so far."""
        return self.failed(
            reason,
            returned_model=returned_model,
            raw_usage=self.usage,
            external_network_calls=0,
            provider_calls=0,
            external_model_calls=1,
            external_process_launches=1,
            route=self.route,
            region=self.region,
            egress=self.egress,
            transport_diagnostic=transport_diagnostic,
        )

    def bind_request_model(self, adapter_id: str, argv: Sequence[str]) -> None:
        """The command line must name exactly the requested model once; record that binding (binary,
        version, command line, allow-list) on the attempt and in its transport diagnostic."""
        model_indices = [
            index for index, item in enumerate(argv) if item == "--model"
        ]
        if (
            len(model_indices) != 1
            or model_indices[0] + 1 >= len(argv)
            or argv[model_indices[0] + 1] != self.requested_model
        ):
            raise ValueError("CODEX_REQUEST_MODEL_ARGV_INVALID")
        self.model_allowlist_sha256 = json_sha256(
            {"allowed_models": [self.requested_model]}
        )
        self.request_model_binding_sha256 = json_sha256(
            {
                "adapter_id": adapter_id,
                "binary_sha256": self.cli_binary_sha256,
                "cli_version": self.cli_version,
                "argv_sha256": json_sha256(list(argv)),
                "model_flag_index": model_indices[0],
                "requested_model": self.requested_model,
                "model_allowlist_sha256": self.model_allowlist_sha256,
                "behavior_sha256": self.behavior_hash,
            }
        )
        self.request_model_binding_status = (
            "EXACT_ARGV_BINARY_AND_FROZEN_ALLOWLIST"
        )
        self.transport_diagnostic = {
            **dict(self.transport_diagnostic or {}),
            "request_model_binding_status": (
                self.request_model_binding_status
            ),
            "request_model_binding_sha256": (
                self.request_model_binding_sha256
            ),
            "model_allowlist_sha256": self.model_allowlist_sha256,
            "cli_binary_sha256": self.cli_binary_sha256,
            "cli_version": self.cli_version,
            "identity_authority_ref": self.cli_identity_authority_ref,
        }


class LiveModelValidationRunner(_EmbeddingExecutionMixin, _SpecializedModelExecutionMixin):
    """Sends the app's model requests: API and command-line chat (execute_structured_chat),
    embeddings, OCR and reranking (the mixins above), the API model list and the minimal
    verification requests. The workflow exams (WorkflowExams, memorive/exams/runner/) hold a runner
    and send their requests through these methods."""

    def _preserve_capacity_response(self, body: bytes, *, partial: bool = False) -> dict[str, Any]:
        """Retain visible output on failure without copying credentials/thinking."""
        visible: Any = body.decode('utf-8', errors='replace')
        if not partial:
            try:
                value = json.loads(visible)
                choices = value.get('choices') or []
                if choices:
                    visible = choices[0].get('message', {}).get('content')
                elif isinstance(value.get('message'), Mapping):
                    visible = value['message'].get('content')
                elif isinstance(value.get('content'), list):
                    visible = ''.join(row.get('text', '') for row in value['content']
                                      if isinstance(row, Mapping) and row.get('type') == 'text')
                else:
                    candidates = value.get('candidates') or []
                    visible = ''.join(part.get('text', '') for row in candidates
                                      for part in row.get('content', {}).get('parts', [])
                                      if isinstance(part, Mapping) and not part.get('thought'))
            except (ValueError, AttributeError, TypeError):
                visible = None
        data = (visible if isinstance(visible, str) else '').encode('utf-8')
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='response-evidence-', suffix='.txt',
                dir=self.scratch_root, delete=False) as stream:
            stream.write(data)
            path = Path(stream.name)
        return {'visible_response_path': str(path), 'visible_response_bytes': len(data),
                'visible_response_sha256': hashlib.sha256(data).hexdigest().upper(),
                'received_body_complete': not partial, 'visible_response_complete': False,
                'reasoning_content_recorded': False}

    def __init__(
        self,
        *,
        credential_resolver: Callable[[str], bytes],
        preferences_loader: Callable[[], Mapping[str, Any]],
        scratch_root: Path,
        http_transport: HttpTransport | None = None,
        process_transport: ProcessTransport | None = None,
        executable_resolver: Callable[[str], str | None] = default_executable_resolver,
    ):
        self._credential_resolver = credential_resolver
        self._preferences_loader = preferences_loader
        self.scratch_root = Path(scratch_root)
        self._call_ledger = CallLedger(self.scratch_root / "model_call_ledger")
        self._http = RecordingTransport(http_transport or UrllibHttpTransport())
        self.process_transport = RecordingTransport(process_transport or BoundedSubprocessTransport())
        self._executable_resolver = executable_resolver

    _base = staticmethod(validation_result)

    def preferences(self) -> tuple[str, str, int]:
        value = self._preferences_loader()
        proxy_mode = value.get("proxy_mode", "SYSTEM")
        proxy_address = value.get("proxy_address", "")
        timeout = value.get("request_timeout_seconds", 60)
        if proxy_mode not in {"SYSTEM", "NONE", "CUSTOM"}:
            proxy_mode = "SYSTEM"
        if not isinstance(proxy_address, str):
            proxy_address = ""
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            timeout = 60
        timeout = min(300, max(5, timeout))
        return str(proxy_mode), proxy_address, timeout

    @scheduled('MODEL_VERIFICATION')
    @recorded_execution
    def verify_api_model(self, service: Mapping[str, Any]) -> Mapping[str, Any]:
        provider = service.get("provider")
        if not isinstance(provider, str):
            return self._base("API", status="NOT_RUN", reason="API_PROVIDER_UNSUPPORTED")
        try:
            profile = _api_profile(service)
        except ValueError:
            return self._base("API", status="NOT_RUN", reason="API_ENDPOINT_INVALID")
        if profile is None:
            return self._base("API", status="NOT_RUN", reason="API_PROVIDER_UNSUPPORTED")
        requested_model = service.get("model_name")
        if not isinstance(requested_model, str) or not requested_model:
            return self._base("API", status="NOT_RUN", reason="API_MODEL_NOT_CONFIGURED")
        result = self._base("API", status="INVALID", reason="API_VALIDATION_FAILED")
        result["requested_model"] = requested_model
        result["model_capability"] = infer_api_model_capability(
            provider, requested_model
        )
        result["validation_endpoint_kind"] = "MODEL_CATALOG"
        credential_ref = service.get("credential_ref")
        if not isinstance(credential_ref, str):
            result["reason"] = "API_CREDENTIAL_UNAVAILABLE"
            return result
        try:
            secret_bytes = self._credential_resolver(credential_ref)
            if not isinstance(secret_bytes, bytes) or not secret_bytes or len(secret_bytes) > 512:
                raise ValueError("API_CREDENTIAL_INVALID")
            secret = secret_bytes.decode("utf-8", errors="strict")
        except (OSError, PermissionError, UnicodeDecodeError, ValueError):
            result["reason"] = "API_CREDENTIAL_UNAVAILABLE"
            return result
        headers = {
            "Accept": "application/json",
            "User-Agent": "Memorive-Model-Validation/1",
            **dict(profile.extra_headers),
        }
        headers[profile.auth_header] = (
            f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
        )
        proxy_mode, proxy_address, timeout = self.preferences()
        started_at = time.monotonic()
        next_url = profile.url
        visited_urls: set[str] = set()
        available_models: list[str] = []
        try:
            for _page_number in range(MAX_CATALOG_PAGES):
                if next_url in visited_urls:
                    result["reason"] = "API_CATALOG_RESPONSE_INVALID"
                    return result
                visited_urls.add(next_url)
                result["external_network_calls"] += 1
                result["provider_calls"] += 1
                try:
                    response = self._http.request(
                        method="GET",
                        url=next_url,
                        headers=headers,
                        body=None,
                        timeout_seconds=timeout,
                        proxy_mode=proxy_mode,
                        proxy_address=proxy_address,
                    )
                except (OSError, TimeoutError, ValueError) as error:
                    from memorive.models.calls.network import error_code
                    result["reason"] = error_code(error)
                    return result
                if response.status_code in {401, 403}:
                    result["reason"] = "API_AUTHENTICATION_REJECTED"
                    return result
                if response.status_code == 429:
                    result["reason"] = "API_RATE_LIMITED"
                    return result
                if response.status_code != 200:
                    result["reason"] = "API_CATALOG_REQUEST_FAILED"
                    return result
                try:
                    document = _catalog_document(response.body)
                    catalog = _catalog_model_ids(profile, document)
                except ValueError:
                    result["reason"] = "API_CATALOG_RESPONSE_INVALID"
                    return result
                available_models = _catalog_choices([*available_models, *catalog])
                matched = _catalog_match(profile, requested_model, catalog)
                from memorive.models.provider_aliases import catalog_alias
                alias = catalog_alias(service, requested_model, catalog) if matched is None else None
                if alias is not None:
                    result.update(status="AVAILABLE", reason="API_MODEL_CATALOG_MATCH",
                                  returned_model=alias['canonical_model'], alias_evidence=alias)
                    return result
                if matched is not None:
                    result.update(
                        status="AVAILABLE",
                        reason="API_MODEL_CATALOG_MATCH",
                        returned_model=matched,
                    )
                    return result
                try:
                    following_url = _next_catalog_url(profile, document)
                except ValueError:
                    result["reason"] = "API_CATALOG_RESPONSE_INVALID"
                    return result
                if following_url is None:
                    result["reason"] = "API_MODEL_NOT_LISTED"
                    result["available_models"] = available_models
                    result["suggested_model"] = _catalog_suggestion(
                        requested_model, available_models
                    )
                    return result
                next_url = following_url
            result["reason"] = "API_CATALOG_PAGINATION_LIMIT"
            return result
        finally:
            result["duration_ms"] = max(
                0, round((time.monotonic() - started_at) * 1000)
            )
            secret = ""
            secret_bytes = b""
            headers.clear()

    @staticmethod
    def safe_environment(
        adapter_id: str,
        cwd: Path,
        proxy_mode: str,
        proxy_address: str,
    ) -> dict[str, str]:
        names = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "USERPROFILE",
            "HOME",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
        adapter_names = {
            "codex_cli": {"CODEX_HOME"},
            "claude_code": {
                "CLAUDE_CONFIG_DIR",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
                "CLAUDE_CODE_ENTRYPOINT",
            },
            "gemini_cli": {"GEMINI_CLI_HOME"},
            "qwen_code": {"QWEN_HOME"},
            "kimi_code": {"KIMI_CODE_HOME"},
            "codebuddy_code": set(),
            "github_copilot_cli": set(),
        }
        names.update(adapter_names.get(adapter_id, set()))
        if proxy_mode == "SYSTEM":
            names.update({"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"})
        environment = {
            name: value
            for name in names
            if isinstance((value := os.environ.get(name)), str)
            and value
            and "\x00" not in value
        }
        environment["TEMP"] = str(cwd)
        environment["TMP"] = str(cwd)
        if proxy_mode == "CUSTOM" and proxy_address:
            environment["HTTP_PROXY"] = proxy_address
            environment["HTTPS_PROXY"] = proxy_address
        if adapter_id == "qwen_code":
            environment["QWEN_RUNTIME_DIR"] = str(cwd / "qwen-runtime")
        elif adapter_id == "codebuddy_code":
            environment["CODEBUDDY_RUNTIME_DIR"] = str(cwd / "codebuddy-runtime")
        elif adapter_id == "github_copilot_cli":
            environment["COPILOT_HOME"] = str(cwd / "copilot-home")
            environment["COPILOT_CACHE_HOME"] = str(cwd / "copilot-cache")
        return environment

    @scheduled('MODEL_VERIFICATION')
    @recorded_execution
    def verify_cli_model(
        self,
        cli_service: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        from memorive.models.cli.templates import is_custom
        if is_custom(cli_service):
            from memorive.models.cli.template_execution import verify
            return verify(self,cli_service,model)
        requested_model = model.get("model_name")
        result = self._base("CLI", status="INVALID", reason="CLI_VALIDATION_FAILED")
        if isinstance(requested_model, str):
            result["requested_model"] = requested_model
        adapter_id = cli_service.get("adapter_id")
        if not isinstance(adapter_id, str):
            result.update(status="NOT_RUN", reason="CLI_ADAPTER_UNSUPPORTED")
            return result
        executable_value = cli_service.get("executable")
        if not isinstance(executable_value, str):
            result["reason"] = "CLI_EXECUTABLE_NOT_FOUND"
            return result
        executable = self._executable_resolver(executable_value)
        if executable is None:
            result["reason"] = "CLI_EXECUTABLE_NOT_FOUND"
            return result
        # Bind the successful verification to the exact executable selected by
        # the same resolver used by workflow execution.  The controller writes
        # this absolute path back only after the frozen target check passes.
        result["resolved_executable"] = executable
        if not isinstance(requested_model, str) or not SAFE_MODEL.fullmatch(requested_model):
            result.update(status="NOT_RUN", reason="CLI_MODEL_NOT_CONFIGURED")
            return result
        thinking_mode = model.get("thinking_mode", "")
        if not isinstance(thinking_mode, str):
            thinking_mode = ""
        proxy_mode, proxy_address, timeout = self.preferences()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="memorive-model-validation-", dir=self.scratch_root
        ) as temporary:
            workspace = Path(temporary).resolve()
            try:
                argv, stdin_bytes = _cli_argv(
                    adapter_id,
                    executable,
                    requested_model,
                    thinking_mode,
                    str(workspace),
                )
            except ValueError as error:
                if str(error) == "CLI_ADAPTER_UNSUPPORTED":
                    result.update(status="NOT_RUN", reason="CLI_ADAPTER_UNSUPPORTED")
                else:
                    result.update(status="NOT_RUN", reason="CLI_MODEL_CONFIGURATION_INVALID")
                return result
            environment = self.safe_environment(
                adapter_id, workspace, proxy_mode, proxy_address
            )
            options = dict(argv=argv, cwd=str(workspace), environment=environment,
                           stdin_bytes=stdin_bytes, timeout_seconds=timeout, shell=False)
            # Recording and cancellation must use the same transport path.
            process, diagnostic = self.process_transport.run_verification(**options)
            result['cli_diagnostic'] = diagnostic
        result["duration_ms"] = process.duration_ms
        if not process.started:
            result["reason"] = "CLI_VERIFICATION_CANCELLED" if diagnostic.get('cancelled') else "CLI_PROCESS_START_FAILED"
            if diagnostic.get('cancelled'):
                result["status"] = "NOT_RUN"
            return result
        result["external_process_launches"] = 1
        result["external_network_calls"] = 1
        result["provider_calls"] = 1
        result["external_model_calls"] = 1
        if diagnostic.get('cancelled'):
            result.update(status='NOT_RUN', reason='CLI_VERIFICATION_CANCELLED')
            return result
        if process.timed_out:
            result["reason"] = "CLI_VALIDATION_TIMEOUT"
            return result
        if process.output_truncated:
            result["reason"] = "CLI_OUTPUT_LIMIT_EXCEEDED"
            return result
        from memorive.models.cli.verification_transport import result_reason
        failure = result_reason(process)
        if failure is not None:
            result["reason"] = failure
            return result
        reported = _reported_models(process.stdout)
        if reported and requested_model not in reported:
            result["reason"] = "CLI_RETURNED_MODEL_MISMATCH"
            return result
        result.update(
            status="AVAILABLE",
            reason=(
                "CLI_MINIMAL_REQUEST_COMPLETED"
                if reported
                else "CLI_MINIMAL_REQUEST_COMPLETED_MODEL_NOT_EXPOSED"
            ),
            returned_model=requested_model if reported else None,
        )
        result['token_usage'] = _normalise_token_usage(_usage_from_cli_output(process.stdout))
        return result

    @recorded_execution
    def execute_structured_chat(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        """Send one structured chat request; every model request of the app comes through here.

        1. Here: check the request, work out the output and time limits, build the prompt bytes.
           A custom command-line tool is run by cli/template_execution.py instead.
        2. _send_api, _send_cli or _send_local sends it by the route of the profile and puts the
           reply, the counts and the route's evidence on the _ChatAttempt.
        3. _chat_result checks the returned model and builds the result with usage, cost and receipt.
        A request that fails at any point ends with _ChatAttempt.failed."""
        if profile_kind not in {"API", "CLI", "LOCAL"}:
            raise ValueError("STRUCTURED_CHAT_PROFILE_KIND_INVALID")
        requested_model = model.get("model_name")
        if (
            not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(response_schema, Mapping)
            or purpose
            not in {
                "conversation_refinement",
                "workflow_model_exam",
                "core_document_processing",
                "report_daily", "report_weekly", "report_monthly", "research_chat", "ai_briefing",
            }
        ):
            raise ValueError("STRUCTURED_CHAT_REQUEST_INVALID")
        output_limit_mode = model.get("max_output_tokens_mode")
        if output_limit_mode not in {None, "EXPLICIT", "PROVIDER_DEFAULT"}:
            raise ValueError("STRUCTURED_CHAT_MAX_OUTPUT_TOKENS_MODE_INVALID")
        if max_output_tokens is not None and (
            isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1
        ):
            raise ValueError("STRUCTURED_CHAT_MAX_OUTPUT_TOKENS_INVALID")
        limits = resolve_call_limits(profile_kind=profile_kind, service=service, model=model,
                                     requested_output_tokens=max_output_tokens)
        effective_max_output_tokens = limits['max_output_tokens']
        if timeout_seconds is None:
            timeout_seconds = limits['timeout_seconds']
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise ValueError("STRUCTURED_CHAT_TIMEOUT_INVALID")
        prompt_bytes = _structured_prompt(prompt, response_schema)
        from memorive.models.cli.templates import is_custom
        if profile_kind=='CLI' and is_custom(service):
            from memorive.models.cli.template_execution import execute
            return execute(self,service,model,prompt_bytes.decode('utf8'),schema=response_schema,purpose=purpose,timeout=timeout_seconds)
        behavior_hash = hashlib.sha256(prompt_bytes).hexdigest().upper()
        started_at = time.monotonic()
        strict_cli_identity_binding = (
            profile_kind == "CLI"
            and model.get("require_exact_cli_identity_binding") is True
        )
        # The state of this one request, filled in by the route that sends it (see _ChatAttempt).
        attempt = _ChatAttempt(
            behavior_hash=behavior_hash,
            effective_max_output_tokens=effective_max_output_tokens,
            limits=limits,
            model=model,
            output_limit_mode=output_limit_mode,
            profile_kind=profile_kind,
            prompt=prompt,
            prompt_bytes=prompt_bytes,
            purpose=purpose,
            requested_model=requested_model,
            response_schema=response_schema,
            service=service,
            started_at=started_at,
            timeout_seconds=timeout_seconds,
            strict_cli_identity_binding=strict_cli_identity_binding,
        )
        # Send it by the route of the profile; a route returns a failed result, or None once it has a reply.
        send = {"API": self._send_api, "CLI": self._send_cli}.get(profile_kind, self._send_local)
        failed = send(attempt)
        if failed is not None:
            return failed
        return self._chat_result(attempt)

    def _send_api(self, attempt: "_ChatAttempt") -> Mapping[str, Any] | None:
        """Send the request to the provider's API; a failed result, or None with the reply on attempt."""
        try:
            profile = _api_profile(attempt.service)
        except ValueError as error:
            raise ValueError("API_ENDPOINT_INVALID") from error
        if profile is None:
            raise ValueError("API_PROVIDER_UNSUPPORTED")
        attempt.api_provider_family = profile.provider_family
        if (attempt.output_limit_mode == "PROVIDER_DEFAULT"
                and attempt.api_provider_family != "ANTHROPIC"
                and attempt.limits["capacity"]["user_output_limit_tokens"] is None):
            # Omit the optional wire field when the user-selected execution
            # policy delegates it. The known provider ceiling remains evidence,
            # not an application-imposed generation cutoff.
            attempt.effective_max_output_tokens = None
            attempt.limits["wire_output_policy"] = "PROVIDER_DEFAULT"
        if (
            attempt.api_provider_family == "ANTHROPIC"
            and attempt.effective_max_output_tokens is None
        ):
            raise ValueError("API_OUTPUT_CAPACITY_REQUIRED")
        credential_ref = attempt.service.get("credential_ref")
        if not isinstance(credential_ref, str) or not credential_ref:
            raise ValueError("API_CREDENTIAL_UNAVAILABLE")
        try:
            secret_bytes = self._credential_resolver(credential_ref)
            if not isinstance(secret_bytes, bytes) or not secret_bytes or len(secret_bytes) > 512:
                raise ValueError("API_CREDENTIAL_INVALID")
            secret = secret_bytes.decode("utf-8", errors="strict")
        except (OSError, PermissionError, UnicodeDecodeError, ValueError) as error:
            raise ValueError("API_CREDENTIAL_UNAVAILABLE") from error
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Memorive-Structured-Chat/1",
            **dict(profile.extra_headers),
        }
        headers[profile.auth_header] = (
            f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
        )
        proxy_mode, proxy_address, configured_timeout = self.preferences()
        timeout = attempt.timeout_seconds
        request_body = _api_chat_request(
            profile,
            model=attempt.requested_model,
            prompt=attempt.prompt,
            response_schema=attempt.response_schema,
            max_output_tokens=attempt.effective_max_output_tokens,
            model_config=attempt.model,
        )
        attempt.transport_diagnostic = {
            "api_request_body_sha256": hashlib.sha256(
                request_body
            ).hexdigest().upper(),
            "max_output_tokens_mode": (
                "PROVIDER_DEFAULT"
                if attempt.effective_max_output_tokens is None
                else "EXPLICIT"
            ),
            "request_max_output_tokens": attempt.effective_max_output_tokens,
            "wire_max_tokens_field_present": (
                b'"max_tokens":' in request_body
                or b'"max_completion_tokens":' in request_body
                or b'"maxOutputTokens":' in request_body
            ),
            "streaming": json.loads(request_body).get("stream") is True,
            "reasoning_effort": json.loads(request_body).get("reasoning_effort"),
            "thinking_option": json.loads(request_body).get("thinking"),
            "enable_thinking": json.loads(request_body).get("enable_thinking"),
        }
        api_max_response_bytes = attempt.model.get("api_max_response_bytes")
        if api_max_response_bytes is not None and (
            isinstance(api_max_response_bytes, bool)
            or not isinstance(api_max_response_bytes, int)
            or api_max_response_bytes <= 0
        ):
            raise ValueError("API_RESPONSE_LIMIT_INVALID")
        attempt.transport_diagnostic["max_response_bytes"] = api_max_response_bytes
        try:
            http_response = self._http.request(
                method="POST",
                url=_api_chat_url(profile, attempt.requested_model),
                headers=headers,
                body=request_body,
                timeout_seconds=timeout,
                proxy_mode=proxy_mode,
                proxy_address=proxy_address,
                max_response_bytes=api_max_response_bytes,
            )
            attempt.external_network_calls = 1
            attempt.provider_calls = 1
            attempt.transport_diagnostic = {
                **dict(attempt.transport_diagnostic or {}),
                **dict(http_response.transport_diagnostic or {}),
                "http_status": http_response.status_code,
            }
            if http_response.status_code != 200:
                _, _, rejection = _api_failure_evidence(http_response.body)
                attempt.transport_diagnostic.update(rejection)
            if http_response.status_code in {401, 403}:
                return attempt.failed(
                    "API_AUTHENTICATION_REJECTED",
                    returned_model=None,
                    raw_usage=None,
                    external_network_calls=1,
                    provider_calls=1,
                    external_model_calls=1,
                    external_process_launches=0,
                    route="EXISTING_SETTINGS_API_TRANSPORT",
                    region="PROVIDER_MANAGED_UNDISCLOSED",
                    egress="PROVIDER_API",
                    transport_diagnostic=attempt.transport_diagnostic,
                )
            if http_response.status_code == 429:
                return attempt.failed(
                    "API_RATE_LIMITED",
                    returned_model=None,
                    raw_usage=None,
                    external_network_calls=1,
                    provider_calls=1,
                    external_model_calls=1,
                    external_process_launches=0,
                    route="EXISTING_SETTINGS_API_TRANSPORT",
                    region="PROVIDER_MANAGED_UNDISCLOSED",
                    egress="PROVIDER_API",
                    transport_diagnostic=attempt.transport_diagnostic,
                )
            if http_response.status_code != 200:
                return attempt.failed(
                    "API_STRUCTURED_CHAT_REQUEST_FAILED",
                    returned_model=None,
                    raw_usage=None,
                    external_network_calls=1,
                    provider_calls=1,
                    external_model_calls=1,
                    external_process_launches=0,
                    route="EXISTING_SETTINGS_API_TRANSPORT",
                    region="PROVIDER_MANAGED_UNDISCLOSED",
                    egress="PROVIDER_API",
                    transport_diagnostic=attempt.transport_diagnostic,
                )
            try:
                attempt.returned_model, attempt.response, attempt.usage = _api_chat_response(
                    profile, http_response.body, attempt.model
                )
            except ValueError as error:
                (
                    recovered_model,
                    recovered_usage,
                    api_diagnostic,
                ) = _api_failure_evidence(
                    http_response.body
                )
                return attempt.failed(
                    str(error),
                    returned_model=recovered_model,
                    raw_usage=recovered_usage,
                    external_network_calls=1,
                    provider_calls=1,
                    external_model_calls=1,
                    external_process_launches=0,
                    route="EXISTING_SETTINGS_API_TRANSPORT",
                    region="PROVIDER_MANAGED_UNDISCLOSED",
                    egress="PROVIDER_API",
                    transport_diagnostic={**dict(attempt.transport_diagnostic or {}), **dict(http_response.transport_diagnostic or {}),
                                          **dict(api_diagnostic),
                                          **self._preserve_capacity_response(http_response.body)},
                )
            attempt.transport_diagnostic = {
                **dict(attempt.transport_diagnostic or {}),
                **dict(http_response.transport_diagnostic or {}),
            }
        except _ResponseInterrupted as error:
            return attempt.failed(
                'API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN',
                returned_model=None, raw_usage=None, external_network_calls=1,
                provider_calls=1, external_model_calls=1, external_process_launches=0,
                route='EXISTING_SETTINGS_API_TRANSPORT', region='PROVIDER_MANAGED_UNDISCLOSED',
                egress='PROVIDER_API', transport_diagnostic={**error.diagnostic,
                    **self._preserve_capacity_response(error.partial, partial=True),
                    'automatic_duplicate_request_safe': False},
            )
        except (OSError, TimeoutError) as error:
            return attempt.failed(
                "API_RESPONSE_STATUS_UNKNOWN",
                returned_model=None,
                raw_usage=None,
                external_network_calls=1,
                provider_calls=1,
                external_model_calls=1,
                external_process_launches=0,
                route="EXISTING_SETTINGS_API_TRANSPORT",
                region="PROVIDER_MANAGED_UNDISCLOSED",
                egress="PROVIDER_API",
                transport_diagnostic={
                    **dict(attempt.transport_diagnostic or {}),
                    'exception_type': type(error).__name__,
                    'dispatch_state': 'MAY_HAVE_BEEN_ACCEPTED',
                    'remote_cancellation_confirmed': False,
                    'automatic_duplicate_request_safe': False,
                },
            )
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()
        attempt.route = "EXISTING_SETTINGS_API_TRANSPORT"

    def _send_cli(self, attempt: "_ChatAttempt") -> Mapping[str, Any] | None:
        """Run the command-line tool; a failed result, or None with the reply on attempt.

        _cli_executable finds the tool (and checks its exact identity when the profile asks for it),
        _run_cli runs it once in a fresh workspace, _cli_exit_failure reads how the run ended and
        _read_cli_reply reads the reply (for Codex its event stream first). Every result after the tool
        ran counts one model call and one process launch (_ChatAttempt.tool_failed).
        """
        adapter_id = attempt.service.get("adapter_id")
        if not isinstance(adapter_id, str):
            raise ValueError("CLI_ADAPTER_UNSUPPORTED")
        attempt.route, attempt.region, attempt.egress = _cli_receipt_route(adapter_id)
        executable = self._cli_executable(attempt)
        process, continuation = self._run_cli(attempt, adapter_id, executable)
        failed = self._cli_exit_failure(attempt, process, continuation)
        if failed is not None:
            return failed
        return self._read_cli_reply(attempt, adapter_id, process, continuation)

    def _cli_executable(self, attempt: "_ChatAttempt") -> str:
        """The tool's resolved executable. When the profile asks for the tool's exact identity, the
        binary's hash, its version and the identity record must be given and match; they go onto the
        attempt."""
        executable_value = attempt.service.get("executable")
        if not isinstance(executable_value, str):
            raise ValueError("CLI_EXECUTABLE_NOT_FOUND")
        executable = self._executable_resolver(executable_value)
        if executable is None:
            raise ValueError("CLI_EXECUTABLE_NOT_FOUND")
        if attempt.strict_cli_identity_binding:
            expected_binary_sha256 = attempt.service.get("cli_binary_sha256")
            cli_version_value = attempt.service.get("cli_version")
            identity_authority_value = attempt.service.get(
                "cli_identity_authority_ref"
            )
            if (
                not isinstance(expected_binary_sha256, str)
                or not SAFE_SHA256.fullmatch(expected_binary_sha256.upper())
                or _resolved_file_sha256(executable)
                != expected_binary_sha256.upper()
                or not isinstance(cli_version_value, str)
                or not cli_version_value
                or not isinstance(identity_authority_value, str)
                or not identity_authority_value
            ):
                raise ValueError(
                    "CLI_STRUCTURED_CHAT_BINARY_IDENTITY_BINDING_INVALID"
                )
            attempt.cli_binary_sha256 = expected_binary_sha256.upper()
            attempt.cli_version = cli_version_value
            attempt.cli_identity_authority_ref = identity_authority_value
        return executable

    def _run_cli(
        self, attempt: "_ChatAttempt", adapter_id: str, executable: str
    ) -> tuple[ProcessResult, Continuation]:
        """Run the tool once in a fresh workspace: the response schema file (Codex), the command line,
        the continuation of an earlier session when the profile keeps one, the check that the command
        names exactly the requested model (when the profile asks), the run, and the session record
        afterwards. Returns the process result and the continuation."""
        thinking_mode = attempt.model.get("thinking_mode", "")
        if not isinstance(thinking_mode, str):
            thinking_mode = ""
        proxy_mode, proxy_address, configured_timeout = self.preferences()
        timeout = attempt.timeout_seconds
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        with structured_chat_workspace(self.scratch_root, profile_kind=attempt.profile_kind,
                service=attempt.service, model=attempt.model, purpose=attempt.purpose) as workspace:
            output_schema_path: str | None = None
            if adapter_id == "codex_cli":
                transport_schema: Mapping[str, Any] = attempt.model.get("prompt_cache_transport_schema", attempt.response_schema)
                if attempt.purpose == "conversation_refinement":
                    transport_schema, attempt.transport_diagnostic = _codex_refinement_transport_schema(transport_schema)
                elif attempt.purpose in {
                    "workflow_model_exam",
                    "core_document_processing",
                    "report_daily", "report_weekly", "report_monthly", "research_chat", "ai_briefing",
                }:
                    (
                        transport_schema,
                        attempt.transport_diagnostic,
                    ) = _codex_transport_schema(transport_schema)
                schema_path = write_response_schema(workspace, json.dumps(
                    transport_schema, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8"))
                output_schema_path = str(schema_path)
            try:
                argv, stdin_bytes = _cli_argv(
                    adapter_id,
                    executable,
                    attempt.requested_model,
                    thinking_mode,
                    str(workspace),
                    attempt.prompt_bytes,
                    output_schema_path,
                )
            except ValueError as error:
                raise ValueError(str(error)) from error
            continuation = Continuation(workspace, attempt.model, attempt.prompt_bytes,
                transport_schema if adapter_id == "codex_cli" else attempt.response_schema)
            if continuation.enabled:
                argv = continuation.prepare_argv(argv)
                stdin_bytes = continuation.prompt_bytes
                attempt.behavior_hash = hashlib.sha256(stdin_bytes).hexdigest().upper()
            if attempt.strict_cli_identity_binding:
                attempt.bind_request_model(adapter_id, argv)
            process = self.process_transport.run(
                argv=argv,
                cwd=str(workspace),
                environment=self.safe_environment(
                    adapter_id, workspace, proxy_mode, proxy_address
                ),
                stdin_bytes=stdin_bytes,
                timeout_seconds=timeout,
                shell=False,
            )
            events_accepted = True
            if continuation.enabled:
                event_summary, event_error = _execution_codex_event_diagnostic(process.stdout, attempt.requested_model)
                events_accepted = (event_error is None and isinstance(event_summary, Mapping)
                    and event_summary.get("finish_status") == "SUCCESS"
                    and event_summary.get("model_identity_grade") != "CONFLICT")
            continuation.complete(process, _structured_document_from_output(process.stdout), attempt.response_schema,
                events_accepted=events_accepted)
            attempt.transport_diagnostic = {**dict(attempt.transport_diagnostic or {}), **continuation.diagnostic}
        return process, continuation

    def _cli_exit_failure(
        self, attempt: "_ChatAttempt", process: ProcessResult, continuation: Continuation
    ) -> Mapping[str, Any] | None:
        """How the run ended: a continuation whose session record failed, a tool that did not start,
        ran out of time, wrote more than allowed or exited with an error gives a failed result.
        Otherwise None, with the run's output sizes and the usage it reported on the attempt."""
        if (continuation.enabled and process.started and process.returncode == 0
                and continuation.diagnostic.get("session_record_status") != "SUCCESS"):
            return attempt.failed(
                "CODEX_CONTINUATION_VALIDATION_FAILED", returned_model=None,
                raw_usage=continuation.attempt_usage or {}, external_network_calls=0,
                provider_calls=0, external_model_calls=1, external_process_launches=1,
                route=attempt.route, region=attempt.region,
                egress=attempt.egress, transport_diagnostic=attempt.transport_diagnostic,
            )
        if not process.started:
            return attempt.failed("CLI_PROCESS_START_FAILED", returned_model=None,
                raw_usage=None, external_network_calls=0, provider_calls=0,
                external_model_calls=0, external_process_launches=0,
                route=attempt.route, region=attempt.region,
                egress=attempt.egress, transport_diagnostic={
                    **dict(attempt.transport_diagnostic or {}), "provider_request_sent": False,
                })
        attempt.external_process_launches = 1
        attempt.transport_diagnostic = {
            **dict(attempt.transport_diagnostic or {}),
            "stdout_total_bytes": process.stdout_total_bytes,
            "stderr_total_bytes": process.stderr_total_bytes,
            "capture_quota_bytes": process.capture_quota_bytes,
            "quota_source": (
                "EXPLICIT_RESOURCE_PROFILE"
                if process.capture_quota_bytes is not None
                else "DISK_SPOOL_NO_GENERIC_PRODUCT_LIMIT"
            ),
        }
        attempt.usage = (continuation.attempt_usage or {}) if continuation.enabled else _usage_from_cli_output(process.stdout)
        if process.timed_out:
            return attempt.tool_failed(
                "CLI_STRUCTURED_CHAT_TIMEOUT", transport_diagnostic=attempt.transport_diagnostic
            )
        if process.output_truncated:
            return attempt.tool_failed(
                "CLI_OUTPUT_LIMIT_EXCEEDED", transport_diagnostic=attempt.transport_diagnostic
            )
        if process.returncode != 0:
            from memorive.models.cli.verification_transport import failure_reason
            failure_diagnostic = {
                **dict(attempt.transport_diagnostic or {}),
                **dict(_cli_failure_diagnostic(process)),
            }
            return attempt.tool_failed(
                failure_reason(process.stderr + b"\n" + process.stdout),
                transport_diagnostic=failure_diagnostic,
            )
        return None

    def _read_cli_reply(
        self, attempt: "_ChatAttempt", adapter_id: str, process: ProcessResult, continuation: Continuation
    ) -> Mapping[str, Any] | None:
        """The reply: for Codex its event stream first (_check_codex_events); then the models the tool
        reports must include the requested one (for the strict purposes a tool other than Codex must
        report one), and the answer document must be there. None with the answer on the attempt, or a
        failed result."""
        if adapter_id == "codex_cli":
            failed = self._check_codex_events(attempt, process, continuation)
            if failed is not None:
                return failed
        reported = _reported_models(process.stdout)
        if reported and attempt.requested_model not in reported:
            return attempt.tool_failed(
                "CLI_RETURNED_MODEL_MISMATCH",
                returned_model=(
                    next(iter(reported)) if len(reported) == 1 else None
                ),
            )
        if (
            attempt.purpose in {"workflow_model_exam", "core_document_processing", "report_daily", "report_weekly", "report_monthly", "research_chat", "ai_briefing"}
            and not reported
            and adapter_id != "codex_cli"
        ):
            return attempt.tool_failed("CLI_RETURNED_MODEL_EVIDENCE_MISSING")
        attempt.response = _structured_document_from_output(process.stdout) or {}
        if continuation.enabled:
            attempt.response = attempt.response.get("payload") if isinstance(attempt.response, dict) and set(attempt.response) == {"payload"} else {}
        if not attempt.response:
            return attempt.tool_failed(
                "STRUCTURED_CHAT_RESPONSE_INVALID",
                returned_model=attempt.requested_model if reported else None,
            )
        # Codex CLI binds the selected model through the exact --model
        # argument.  Current JSONL may omit a redundant model field; a
        # successful process with no contradictory identity still binds
        # the returned identity to that exact invocation.
        attempt.returned_model = attempt.requested_model
        return None

    def _check_codex_events(
        self, attempt: "_ChatAttempt", process: ProcessResult, continuation: Continuation
    ) -> Mapping[str, Any] | None:
        """Codex's JSONL event stream: it must parse, the turn must finish successfully and name no
        other model; its parser identity goes into the transport diagnostic and its token usage into
        the attempt (unless a continuation already counted it). None, or a failed result."""
        execution_diagnostic, execution_error = _execution_codex_event_diagnostic(
            process.stdout, attempt.requested_model
        )
        if execution_error is not None and attempt.purpose in {
            "workflow_model_exam", "conversation_refinement",
            "core_document_processing",
            "report_daily", "report_weekly", "report_monthly", "research_chat", "ai_briefing",
        }:
            return attempt.tool_failed(
                execution_error,
                transport_diagnostic={
                    **dict(attempt.transport_diagnostic or {}),
                    "execution_parser_error": execution_error,
                    "stdout_sha256": hashlib.sha256(
                        process.stdout
                    ).hexdigest().upper(),
                },
            )
        if execution_error is not None:
            attempt.transport_diagnostic = {
                **dict(attempt.transport_diagnostic or {}),
                "execution_parser_error": execution_error,
                "execution_parser_compatibility_fallback": True,
                "stdout_sha256": hashlib.sha256(
                    process.stdout
                ).hexdigest().upper(),
            }
        if not isinstance(execution_diagnostic, Mapping):
            return None
        attempt.transport_diagnostic = {
            **dict(attempt.transport_diagnostic or {}),
            "execution_parser_version": execution_diagnostic.get(
                "parser_version"
            ),
            "execution_events_sha256": execution_diagnostic.get(
                "events_sha256"
            ),
            "execution_model_identity_grade": execution_diagnostic.get(
                "model_identity_grade"
            ),
            "execution_returned_model": execution_diagnostic.get(
                "returned_model"
            ),
            "execution_usage_status": execution_diagnostic.get(
                "usage_status"
            ),
        }
        returned_model = execution_diagnostic.get("returned_model")
        returned_model = returned_model if isinstance(returned_model, str) else None
        if execution_diagnostic.get("finish_status") != "SUCCESS":
            return attempt.tool_failed(
                "CODEX_JSONL_FINISH_NOT_SUCCESS",
                returned_model=returned_model,
                transport_diagnostic=attempt.transport_diagnostic,
            )
        if execution_diagnostic.get("model_identity_grade") == "CONFLICT":
            return attempt.tool_failed(
                "CLI_RETURNED_MODEL_MISMATCH",
                returned_model=returned_model,
                transport_diagnostic=attempt.transport_diagnostic,
            )
        execution_usage = execution_diagnostic.get("token_usage")
        if isinstance(execution_usage, Mapping) and not continuation.enabled:
            attempt.usage = {
                **dict(attempt.usage),
                **dict(execution_usage),
            }
        return None

    def _send_local(self, attempt: "_ChatAttempt") -> Mapping[str, Any] | None:
        """Ask the local model; a failed result, or None with the reply on attempt."""
        if (
            attempt.service.get("endpoint_kind") != "ollama"
            or attempt.service.get("structured_chat_adapter")
            != "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
            or attempt.service.get("execution_eligible") is not True
            or attempt.service.get("exact_identity_available") is not True
            or not isinstance(attempt.service.get("chat_endpoint"), str)
            or not isinstance(attempt.service.get("model_digest"), str)
            or not SAFE_SHA256.fullmatch(str(attempt.service["model_digest"]).upper())
        ):
            raise ValueError("LOCAL_STRUCTURED_CHAT_PROFILE_INVALID")
        from memorive.models.gateway.local_structured_chat import LocalStructuredChatAdapter, _default_open
        from memorive.models.calls.ledger import recording_urlopen

        timeout = attempt.timeout_seconds
        local_num_ctx = (
            attempt.limits['capacity']['effective_capacity']['shared_context_tokens']
            if attempt.limits['capacity']['machine_safe_input_tokens'] is not None else None
        )
        local_binding = {
            "schema_version": "EvaluationAssetsLocalStructuredChatBinding-v1",
            "endpoint": attempt.service["chat_endpoint"],
            "requested_model": attempt.requested_model,
            "expected_model_digest": str(attempt.service["model_digest"]).upper(),
            "think": False,
            "temperature": 0,
            "seed": 0,
            "cloud_fallback": False,
        }
        if attempt.effective_max_output_tokens is not None:
            local_binding["num_predict"] = attempt.effective_max_output_tokens
        if local_num_ctx is not None:
            local_binding["num_ctx"] = local_num_ctx
        attempt.transport_diagnostic = {"local_num_ctx": local_num_ctx, "local_num_predict": attempt.effective_max_output_tokens, "capacity_profile_specific": True}
        outcome = LocalStructuredChatAdapter(urlopen=recording_urlopen(_default_open)).execute(
            prompt=attempt.prompt_bytes.decode("utf-8"),
            schema=attempt.response_schema,
            binding=local_binding,
            purpose=attempt.purpose,
            timeout_seconds=timeout,
        )
        local_receipt = outcome.receipt
        local_model_calls = int(
            local_receipt.get("local_model_request_count") or 0
        )
        attempt.usage = (
            local_receipt.get("usage")
            if isinstance(local_receipt.get("usage"), Mapping)
            else {}
        )
        if not outcome.success or not isinstance(outcome.response, Mapping):
            return attempt.failed(
                ('STRUCTURED_CHAT_JSON_TRUNCATED' if outcome.error_code == 'MODEL_OUTPUT_TRUNCATED'
                 else outcome.error_code or "LOCAL_STRUCTURED_CHAT_FAILED"),
                returned_model=(
                    local_receipt.get("returned_model")
                    if isinstance(local_receipt.get("returned_model"), str)
                    else None
                ),
                raw_usage=attempt.usage,
                external_network_calls=0,
                provider_calls=0,
                external_model_calls=local_model_calls,
                external_process_launches=0,
                route="OLLAMA_LOOPBACK",
                region="LOCAL_MACHINE",
                egress="LOOPBACK_ONLY",
                transport_diagnostic={
                    **({'finish_reason': 'length'} if outcome.error_code == 'MODEL_OUTPUT_TRUNCATED' else {}),
                    **(self._preserve_capacity_response(json.dumps(outcome.raw_response).encode('utf-8'))
                       if isinstance(getattr(outcome, 'raw_response', None), Mapping) else {}),
                    **attempt.transport_diagnostic,
                    "local_receipt_sha256": local_receipt.get(
                        "receipt_sha256"
                    ),
                    "model_digest": local_receipt.get("model_digest"),
                    "ollama_version": local_receipt.get("ollama_version"),
                    "local_metadata_request_count": local_receipt.get(
                        "local_metadata_request_count"
                    ),
                    "local_model_request_count": local_model_calls,
                },
            )
        attempt.response = dict(outcome.response)
        attempt.returned_model = str(local_receipt.get("returned_model") or "")
        attempt.route = "OLLAMA_LOOPBACK"
        attempt.transport_diagnostic.update(
            {
                "local_receipt_sha256": local_receipt.get(
                    "receipt_sha256"
                ),
                "model_digest": local_receipt.get("model_digest"),
                "ollama_version": local_receipt.get("ollama_version"),
                "local_metadata_request_count": local_receipt.get(
                    "local_metadata_request_count"
                ),
                "local_model_request_count": local_model_calls,
            }
        )

    def _chat_result(self, attempt: "_ChatAttempt") -> Mapping[str, Any]:
        """The result of a request whose route got a reply: the returned model checked, usage, cost and the receipt."""
        from memorive.models.provider_aliases import catalog_alias
        provider_alias = catalog_alias(attempt.service, attempt.requested_model, [attempt.returned_model]) if attempt.profile_kind == 'API' else None
        if (unicodedata.normalize("NFKC", attempt.returned_model).strip()
            != unicodedata.normalize("NFKC", attempt.requested_model).strip() and provider_alias is None):
            return attempt.failed(
                "STRUCTURED_CHAT_RETURNED_MODEL_MISMATCH",
                returned_model=attempt.returned_model,
                raw_usage=attempt.usage,
                external_network_calls=attempt.external_network_calls,
                provider_calls=attempt.provider_calls,
                external_model_calls=1,
                external_process_launches=attempt.external_process_launches,
                route=attempt.route,
                region=(
                    attempt.region
                    if attempt.profile_kind == "CLI"
                    else (
                        "LOCAL_MACHINE"
                        if attempt.profile_kind == "LOCAL"
                        else "PROVIDER_MANAGED_UNDISCLOSED"
                    )
                ),
                egress=(
                    attempt.egress
                    if attempt.profile_kind == "CLI"
                    else ("LOOPBACK_ONLY" if attempt.profile_kind == "LOCAL" else "PROVIDER_API")
                ),
            )
        token_usage = _normalise_token_usage(attempt.usage)
        if (
            attempt.purpose in {"workflow_model_exam", "core_document_processing", "report_daily", "report_weekly", "report_monthly", "research_chat", "ai_briefing"}
            and not token_usage
        ):
            return attempt.failed(
                "STRUCTURED_CHAT_TOKEN_EVIDENCE_MISSING",
                returned_model=attempt.returned_model,
                raw_usage=attempt.usage,
                external_network_calls=attempt.external_network_calls,
                provider_calls=attempt.provider_calls,
                external_model_calls=1,
                external_process_launches=attempt.external_process_launches,
                route=attempt.route,
                region=(
                    attempt.region
                    if attempt.profile_kind == "CLI"
                    else (
                        "LOCAL_MACHINE"
                        if attempt.profile_kind == "LOCAL"
                        else "PROVIDER_MANAGED_UNDISCLOSED"
                    )
                ),
                egress=(
                    attempt.egress
                    if attempt.profile_kind == "CLI"
                    else ("LOOPBACK_ONLY" if attempt.profile_kind == "LOCAL" else "PROVIDER_API")
                ),
            )
        if attempt.purpose == "workflow_model_exam":
            observed_completion_tokens = int(
                token_usage.get("completion_tokens") or 0
            )
            attempt.transport_diagnostic = {
                **dict(attempt.transport_diagnostic or {}),
                "output_limit_contract": workflow_exam_output_limit_contract(),
                "observed_completion_tokens": observed_completion_tokens,
                "calibrated_reply_token_ceiling": (
                    WORKFLOW_EXAM_REPLY_TOKEN_CEILING
                ),
                "calibrated_reply_token_ceiling_exceeded": (
                    observed_completion_tokens
                    > WORKFLOW_EXAM_REPLY_TOKEN_CEILING
                ),
                "reply_length_quality_semantics": (
                    "EVIDENCE_RETAINED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
                ),
            }
        estimated_cost_cny = (
            _api_estimated_cost_cny(
                attempt.requested_model, attempt.model, token_usage
            )
            if attempt.profile_kind == "API"
            else None
        )
        if attempt.profile_kind == "CLI":
            cost_evidence = "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
            region = attempt.region
            egress = attempt.egress
        elif attempt.profile_kind == "LOCAL":
            estimated_cost_cny = 0.0
            cost_evidence = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            region = "LOCAL_MACHINE"
            egress = "LOOPBACK_ONLY"
        elif estimated_cost_cny is not None:
            cost_evidence = (
                "FROZEN_TARGET_PRICING_PROFILE_ESTIMATE"
                if attempt.model.get("pricing_evidence_ref")
                else "FROZEN_Quality_PUBLIC_PRICING_PROFILE_2026-07-30_ESTIMATE"
            )
            region = "PROVIDER_MANAGED_UNDISCLOSED"
            egress = "PROVIDER_API"
        else:
            cost_evidence = "NOT_REPORTED_BY_TRANSPORT"
            region = "PROVIDER_MANAGED_UNDISCLOSED"
            egress = "PROVIDER_API"
        duration_ms = max(0, round((time.monotonic() - attempt.started_at) * 1000))
        from memorive.models.gateway.cache_metrics import cache_metrics
        attempt.transport_diagnostic = {**dict(attempt.transport_diagnostic or {}),
            'prompt_cache_metrics': cache_metrics(dict(attempt.usage or {}), duration_ms=duration_ms,
                estimated_cost=estimated_cost_cny)}
        response_hash = hashlib.sha256(
            json.dumps(
                attempt.response,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        return {
            "schema_version": "SettingsStructuredChatRunnerResult-v1",
            "status": "PASS",
            "reason": "STRUCTURED_CHAT_COMPLETED",
            "requested_model": attempt.requested_model,
            "returned_model": attempt.returned_model,
            "response": attempt.response,
            "duration_ms": duration_ms,
            "external_network_calls": attempt.external_network_calls,
            "provider_calls": attempt.provider_calls,
            "external_model_calls": 1,
            "external_process_launches": attempt.external_process_launches,
            "execution_receipt": {
                "schema_version": "SettingsStructuredChatExecutionReceipt-v2",
                "status": "PASS",
                "profile_kind": attempt.profile_kind,
                "purpose": attempt.purpose,
                **({'provider_model_alias': {**provider_alias, 'endpoint_origin': 'https://api.deepseek.com'}} if provider_alias is not None else {}),
                "route": attempt.route,
                "requested_model": attempt.requested_model,
                "returned_model": attempt.returned_model,
                "behavior_sha256": attempt.behavior_hash,
                "response_sha256": response_hash,
                "duration_ms": duration_ms,
                "token_usage": token_usage,
                "actual_cost": None,
                "estimated_cost_cny": estimated_cost_cny,
                "cost_evidence": cost_evidence,
                "region": region,
                "egress": egress,
                "model_identity_evidence": (
                    "DIRECT_CLI_REQUEST_BINDING_V1"
                    if attempt.strict_cli_identity_binding
                    else "CLI_EXACT_MODEL_ARGUMENT_AND_SUCCESSFUL_PROCESS"
                    if attempt.profile_kind == "CLI"
                    else (
                        "OLLAMA_EXACT_NAME_DIGEST_AND_RESPONSE_MODEL"
                        if attempt.profile_kind == "LOCAL"
                        else "PROVIDER_RESPONSE_MODEL_FIELD"
                    )
                ),
                "request_model_binding_status": attempt.request_model_binding_status,
                "request_model_binding_sha256": attempt.request_model_binding_sha256,
                "model_allowlist_sha256": attempt.model_allowlist_sha256,
                "identity_authority_ref": attempt.cli_identity_authority_ref,
                "cli_binary_sha256": attempt.cli_binary_sha256,
                "cli_version": attempt.cli_version,
                "transport_diagnostic": attempt.transport_diagnostic,
                "resource_limits": attempt.limits,
                "output_budget": {
                    "effective_sent_tokens": attempt.effective_max_output_tokens,
                    "source": (
                        "CALLER_VERIFIED_PROFILE"
                        if attempt.effective_max_output_tokens is not None
                        else (
                            "CLI_PROVIDER_MANAGED"
                            if attempt.profile_kind == "CLI"
                            else "PROVIDER_OR_LOCAL_RUNTIME_MANAGED"
                        )
                    ),
                    "product_hard_limit_added": False,
                },
                "max_output_tokens_mode": (
                    "PROVIDER_DEFAULT"
                    if attempt.effective_max_output_tokens is None
                    else "EXPLICIT"
                ),
                "request_max_output_tokens": attempt.effective_max_output_tokens,
            },
        }
