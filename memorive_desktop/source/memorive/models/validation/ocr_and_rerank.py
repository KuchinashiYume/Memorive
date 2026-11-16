"""OCR and reranking requests: _SpecializedModelExecutionMixin, part of LiveModelValidationRunner.
execute_ocr_image sends a page image to an OCR model (DeepSeek OCR output loses its grounding lines);
execute_rerank scores passages against a query.
"""
from __future__ import annotations

from memorive.models.calls.ledger import recorded_execution
from memorive.models.gateway.prompt_cache import cache_usage
import base64
from dataclasses import field
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)
from memorive.models.endpoints import SAFE_MODEL, SAFE_SHA256, _api_profile, json_sha256
from memorive.models.gateway.resource_limits import resolve_call_limits

from memorive.models.validation.api_route import _api_chat_url
from memorive.models.validation.cli_route import (
    _cli_argv,
    _cli_failure_diagnostic,
    _codex_agent_message_from_events,
    _execution_codex_event_diagnostic,
    _resolved_file_sha256,
)
from memorive.models.validation.transport import _NoRedirect
from memorive.models.validation.usage import _nonnegative_int, _normalise_token_usage


# Metadata limits are separate from generated model output. None delegates
# generated response capacity to the provider/runtime (capacity policy).
MAX_HTTP_BODY_BYTES = 1024 * 1024


MAX_WORKFLOW_EXAM_HTTP_BODY_BYTES = None


_DEEPSEEK_OCR_GROUNDING_LINE_RE = re.compile(
    r"^[ \t]*<\|ref\|>[^\r\n]{1,120}<\|/ref\|>[ \t]*"
    r"<\|det\|>[ \t]*\[\[[0-9.,\- \t\[\]]+\]\]"
    r"[ \t]*<\|/det\|>[ \t]*(?:\r?\n)?",
    re.MULTILINE,
)


DEEPSEEK_OCR_NORMALIZATION_REVISION = (
    "Desktop_DEEPSEEK_OCR_GROUNDING_LINE_NORMALIZATION_V1"
)


def _normalize_deepseek_ocr_markdown(value: object) -> object:
    """Reuse the production SiliconFlow OCR grounding-line normalization."""

    if not isinstance(value, str):
        return value
    return _DEEPSEEK_OCR_GROUNDING_LINE_RE.sub("", value).strip()


class _SpecializedModelExecutionMixin:
    """Capability-specific OCR and reranker transports for model exams.

    These methods deliberately do not route either capability through the
    structured-chat or embedding adapters.  A completed request therefore
    proves the capability that the workflow node actually consumes.
    """

    @staticmethod
    def _specialized_failure(
        *,
        schema_version: str,
        reason: str,
        requested_model: str | None,
        returned_model: str | None,
        profile_kind: str,
        started: float,
        model_calls: int,
        metadata_calls: int = 0,
        process_launches: int = 0,
    ) -> dict[str, Any]:
        accepted_reason = reason if reason and len(reason) <= 160 else "SPECIALIZED_MODEL_CALL_FAILED"
        external_budget_calls = model_calls if profile_kind in {"API", "CLI"} else 0
        local_model_calls = model_calls if profile_kind == "LOCAL" else 0
        return {
            "schema_version": schema_version,
            "status": "FAILED",
            "reason": accepted_reason,
            "requested_model": requested_model,
            "returned_model": returned_model,
            "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
            "external_network_calls": model_calls if profile_kind == "API" else 0,
            "provider_calls": model_calls if profile_kind == "API" else 0,
            # ``external_model_calls`` is retained as the legacy physical-call
            # counter consumed by older Quality executors.  Successor budget
            # enforcement must use ``external_budget_calls`` so an unlimited
            # loopback/local run cannot consume an external-call allowance.
            "external_model_calls": model_calls,
            "physical_model_calls": model_calls,
            "external_budget_calls": external_budget_calls,
            "local_model_calls": local_model_calls,
            "external_process_launches": process_launches,
            "local_metadata_calls": metadata_calls,
        }

    @recorded_execution
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
        started = time.monotonic()
        requested_model = model.get("model_name")
        output_allocation_ceiling = (
            WORKFLOW_EXAM_REPLY_TOKEN_CEILING
        )
        if (
            profile_kind not in {"API", "CLI", "LOCAL"}
            or not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not isinstance(image_bytes, bytes)
            or not image_bytes
            or len(image_bytes) > 8 * 1024 * 1024
            or mime_type not in {"image/png", "image/jpeg", "image/webp"}
            or not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt.encode("utf-8")) > 65_536
            or purpose not in {"workflow_model_exam", "core_document_processing", "research_attachment"}
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 5 <= timeout_seconds <= 600
            or (
                max_output_tokens is not None
                and (
                    isinstance(max_output_tokens, bool)
                    or not isinstance(max_output_tokens, int)
                    or not 1 <= max_output_tokens <= output_allocation_ceiling
                )
            )
        ):
            raise ValueError("OCR_IMAGE_REQUEST_INVALID")
        if profile_kind=='CLI' and service.get('adapter_id')=='command_template':
            from memorive.models.cli.template_execution import execute
            result=execute(self,service,model,prompt,purpose=purpose,timeout=timeout_seconds,image=image_bytes)
            result['schema_version']='SettingsOcrImageRunnerResult-v1'
            text=result.pop('response','')
            result.update(text=text,raw_text=text,normalization_revision='IDENTITY',normalization_applied=False,finish_reason='unknown',
                physical_model_calls=result['external_model_calls'],external_budget_calls=result['external_model_calls'],local_model_calls=0,local_metadata_calls=0)
            receipt=result['execution_receipt'];receipt.update(schema_version='SettingsOcrImageExecutionReceipt-v1',
                image_sha256=hashlib.sha256(image_bytes).hexdigest().upper(),raw_text_sha256=hashlib.sha256(text.encode('utf8')).hexdigest().upper(),
                normalized_text_sha256=hashlib.sha256(text.encode('utf8')).hexdigest().upper(),normalization_revision='IDENTITY',normalization_applied=False,
                model_identity_grade='CLI_REPORTED_MODEL' if receipt['returned_model'] else 'REQUEST_BOUND_RETURNED_UNKNOWN',
                provider_event_model_identity_grade='EXACT_RETURNED' if receipt['returned_model'] else 'UNAVAILABLE')
            return result
        behavior_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "model": requested_model,
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest().upper(),
                    "mime_type": mime_type,
                    "prompt": prompt,
                    "purpose": purpose,
                    "max_output_tokens": max_output_tokens,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        transport_diagnostic = {}
        secret = ""
        secret_bytes = b""
        headers: dict[str, str] = {}
        model_calls = 0
        metadata_calls = 0
        process_launches = 0
        returned_model: str | None = requested_model if profile_kind == "LOCAL" else None
        provider_identity_grade: str | None = None
        effective_identity_grade: str | None = None
        request_model_binding_status: str | None = None
        request_model_binding_sha256: str | None = None
        model_allowlist_sha256: str | None = None
        identity_authority_ref: str | None = None
        try:
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            if profile_kind == "API":
                profile = _api_profile(service)
                if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
                    raise ValueError("OCR_API_PROFILE_UNSUPPORTED")
                endpoint = _api_chat_url(profile, requested_model)
                credential_ref = service.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret_bytes = self._credential_resolver(credential_ref)
                if not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret = secret_bytes.decode("utf-8", errors="strict")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Memorive-OCR-Exam/1",
                    **dict(profile.extra_headers),
                }
                headers[profile.auth_header] = (
                    f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
                )
                detail = model.get("image_detail", "high")
                if detail not in {"auto", "low", "high"}:
                    raise ValueError("OCR_IMAGE_DETAIL_INVALID")
                payload: dict[str, Any] = {
                    "model": requested_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{image_base64}",
                                        "detail": detail,
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                    "stream": False,
                }
                from memorive.models.request_options import reasoning_profile, resolve_reasoning_options, apply_reasoning_options
                control = reasoning_profile(requested_model, profile.url, profile.provider_family)
                options = resolve_reasoning_options(control, model)
                if options is not None:
                    apply_reasoning_options(payload, options)
                if max_output_tokens is not None:
                    field = control['output_field'] if control else model.get('max_output_tokens_wire_field', 'max_tokens')
                    if field not in {'max_tokens','max_completion_tokens'}:
                        raise ValueError('API_MAX_OUTPUT_TOKENS_FIELD_INVALID')
                    payload[field] = max_output_tokens
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                proxy_mode, proxy_address, _ = self.preferences()
                model_calls = 1
                http_response = self._http.request(
                    method="POST",
                    url=endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=timeout_seconds,
                    proxy_mode=proxy_mode,
                    proxy_address=proxy_address,
                    max_response_bytes=(
                        MAX_WORKFLOW_EXAM_HTTP_BODY_BYTES
                        if purpose == "workflow_model_exam"
                        else MAX_HTTP_BODY_BYTES
                    ),
                )
                status_code = http_response.status_code
                raw = http_response.body
                if status_code != 200:
                    from memorive.models.request_options import safe_error_details
                    transport_diagnostic = {'http_status': status_code,
                        'provider_body_sha256': hashlib.sha256(raw).hexdigest()}
                    try:
                        failed_document = json.loads(raw.decode('utf8'))
                        if isinstance(failed_document, Mapping):
                            transport_diagnostic.update(safe_error_details(failed_document))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                if status_code in {401, 403}:
                    raise ValueError("API_AUTHENTICATION_REJECTED")
                if status_code == 429:
                    raise ValueError("API_RATE_LIMITED")
                if status_code != 200:
                    raise ValueError(f"OCR_HTTP_{status_code}")
                document = json.loads(raw.decode("utf-8", errors="strict"))
                choices = document.get("choices") if isinstance(document, Mapping) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                    raise ValueError("OCR_RESPONSE_INVALID")
                message = choices[0].get("message")
                content: Any = message.get("content") if isinstance(message, Mapping) else None
                if isinstance(content, list):
                    content = "\n".join(
                        str(item.get("text", ""))
                        for item in content
                        if isinstance(item, Mapping) and item.get("type") == "text"
                    )
                finish_reason = choices[0].get("finish_reason")
                if finish_reason == "length":
                    raise ValueError("OCR_OUTPUT_TRUNCATED")
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model != requested_model:
                    raise ValueError("OCR_RETURNED_MODEL_MISMATCH")
                returned_model = str(observed_model)
                raw_usage = document.get("usage") if isinstance(document.get("usage"), Mapping) else {}
                token_usage = _normalise_token_usage(raw_usage)
                route = "EXISTING_SETTINGS_API_MULTIMODAL_TRANSPORT"
                region = "PROVIDER_MANAGED_UNDISCLOSED"
                egress = "PROVIDER_API"
                identity_evidence = "PROVIDER_RESPONSE_MODEL_FIELD"
                provider_identity_grade = "VERIFIED"
                effective_identity_grade = "VERIFIED"
                estimated_cost_cny = (
                    0.0 if requested_model == "deepseek-ai/DeepSeek-OCR" else None
                )
                cost_evidence = (
                    "FROZEN_Quality_OCR_ZERO_PRICE_PROFILE_2026-07-31"
                    if estimated_cost_cny == 0.0
                    else "ATTEMPT_COST_EVIDENCE_UNAVAILABLE"
                )
            elif profile_kind == "LOCAL":
                if (
                    service.get("endpoint_kind") != "ollama"
                    or service.get("execution_eligible") is not True
                    or service.get("exact_identity_available") is not True
                    or not isinstance(service.get("chat_endpoint"), str)
                    or not isinstance(service.get("model_digest"), str)
                    or not SAFE_SHA256.fullmatch(str(service["model_digest"]).upper())
                ):
                    raise ValueError("LOCAL_OCR_PROFILE_INVALID")
                parsed = urlsplit(str(service["chat_endpoint"]))
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path != "/api/chat"
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("LOCAL_OCR_ENDPOINT_INVALID")
                # Research attachments use the same declared capacity policy as
                # ordinary local calls. An old exam allocation is not a user
                # output limit; absent limits remain managed by the runtime.
                endpoint = str(service["chat_endpoint"])
                tags_endpoint = urlunsplit((parsed.scheme, parsed.netloc, "/api/tags", "", ""))
                opener = build_opener(ProxyHandler({}), _NoRedirect())
                metadata_calls = 1
                from memorive.models.calls.ledger import recording_urlopen
                opener.open = recording_urlopen(opener.open)
                with opener.open(Request(tags_endpoint, method="GET"), timeout=min(10, timeout_seconds)) as response:
                    tags_raw = response.read(2 * 1024 * 1024 + 1)
                if len(tags_raw) > 2 * 1024 * 1024:
                    raise ValueError("LOCAL_OCR_TAGS_RESPONSE_TOO_LARGE")
                tags = json.loads(tags_raw.decode("utf-8"))
                rows = tags.get("models") if isinstance(tags, Mapping) else None
                matched = next(
                    (
                        row
                        for row in (rows if isinstance(rows, list) else [])
                        if isinstance(row, Mapping)
                        and (row.get("model") or row.get("name")) == requested_model
                    ),
                    None,
                )
                if (
                    not isinstance(matched, Mapping)
                    or str(matched.get("digest") or "").upper()
                    != str(service["model_digest"]).upper()
                ):
                    raise ValueError("LOCAL_OCR_MODEL_IDENTITY_MISMATCH")
                limits = resolve_call_limits(profile_kind=profile_kind, service=service, model=model,
                                             requested_output_tokens=max_output_tokens)
                local_context = (limits['capacity']['effective_capacity']['shared_context_tokens']
                                 if limits['capacity']['machine_safe_input_tokens'] is not None else None)
                max_output_tokens = limits['max_output_tokens']
                timeout_seconds = limits['timeout_seconds']
                payload = {
                    "model": requested_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [image_base64],
                        }
                    ],
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0,
                        "seed": 0,
                        **({"num_ctx": local_context} if local_context is not None else {}),
                        **({"num_predict": max_output_tokens} if max_output_tokens is not None else {}),
                    },
                }
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
                request = Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
                model_calls = 1
                try:
                    with opener.open(request, timeout=timeout_seconds) as response:
                        status_code = int(response.status)
                        raw = response.read()
                except HTTPError as error:
                    status_code = int(error.code)
                    raw = error.read()
                    message = ""
                    try:
                        failed_document = json.loads(raw.decode("utf-8", errors="strict"))
                        if isinstance(failed_document, Mapping) and isinstance(
                            failed_document.get("error"), str
                        ):
                            message = re.sub(
                                r"[^A-Za-z0-9 _.,:;()/-]+",
                                "",
                                str(failed_document["error"]),
                            )[:96].strip()
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        message = ""
                    suffix = f":{message}" if message else ""
                    raise ValueError(f"LOCAL_OCR_HTTP_{status_code}{suffix}") from error
                if status_code != 200:
                    raise ValueError(f"LOCAL_OCR_HTTP_{status_code}")
                document = json.loads(raw.decode("utf-8", errors="strict"))
                message = document.get("message") if isinstance(document, Mapping) else None
                content = message.get("content") if isinstance(message, Mapping) else None
                finish_reason = document.get("done_reason") if isinstance(document, Mapping) else None
                if finish_reason == "length":
                    raise ValueError("OCR_OUTPUT_TRUNCATED")
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model != requested_model:
                    raise ValueError("LOCAL_OCR_RETURNED_MODEL_MISMATCH")
                returned_model = requested_model
                token_usage = _normalise_token_usage(document)
                route = "OLLAMA_MULTIMODAL_LOOPBACK"
                region = "LOCAL_MACHINE"
                egress = "LOOPBACK_ONLY"
                identity_evidence = "OLLAMA_EXACT_NAME_AND_DIGEST_PRECALL_BINDING"
                provider_identity_grade = "VERIFIED"
                effective_identity_grade = "VERIFIED"
                estimated_cost_cny = 0.0
                cost_evidence = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            else:
                if max_output_tokens is not None:
                    raise ValueError("CLI_OCR_EXPLICIT_OUTPUT_LIMIT_UNSUPPORTED")
                adapter_id = service.get("adapter_id")
                executable_value = service.get("executable")
                thinking_mode = model.get("thinking_mode")
                if (
                    adapter_id != "codex_cli"
                    or not isinstance(executable_value, str)
                    or not isinstance(thinking_mode, str)
                    or not thinking_mode
                ):
                    raise ValueError("CLI_OCR_PROFILE_INVALID")
                executable = self._executable_resolver(executable_value)
                if executable is None:
                    raise ValueError("CLI_EXECUTABLE_NOT_FOUND")
                from memorive.models.cli.image_identity import bind_saved_cli_identity
                proxy_mode, proxy_address, _ = self.preferences()
                self.scratch_root.mkdir(parents=True, exist_ok=True)
                service, identity_probes = bind_saved_cli_identity(
                    service, executable, process=self.process_transport,
                    environment=self.safe_environment(
                        adapter_id, self.scratch_root, proxy_mode, proxy_address),
                    root=self.scratch_root)
                metadata_calls += identity_probes
                expected_binary_sha256 = service.get("cli_binary_sha256")
                cli_version = service.get("cli_version")
                identity_authority_ref = service.get("cli_identity_authority_ref")
                if (
                    not isinstance(expected_binary_sha256, str)
                    or not SAFE_SHA256.fullmatch(expected_binary_sha256.upper())
                    or _resolved_file_sha256(executable)
                    != expected_binary_sha256.upper()
                    or not isinstance(cli_version, str)
                    or not cli_version
                    or not isinstance(identity_authority_ref, str)
                    or not identity_authority_ref
                ):
                    raise ValueError("CLI_OCR_BINARY_IDENTITY_BINDING_INVALID")
                proxy_mode, proxy_address, _ = self.preferences()
                self.scratch_root.mkdir(parents=True, exist_ok=True)
                suffix = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                }[mime_type]
                with tempfile.TemporaryDirectory(
                    prefix="memorive-ocr-cli-", dir=self.scratch_root
                ) as temporary:
                    workspace = Path(temporary).resolve()
                    image_path = workspace / f"input{suffix}"
                    with image_path.open("xb") as stream:
                        stream.write(image_bytes)
                    argv, stdin_bytes = _cli_argv(
                        adapter_id,
                        executable,
                        requested_model,
                        thinking_mode,
                        str(workspace),
                        prompt.encode("utf-8"),
                        None,
                        str(image_path),
                    )
                    model_indices = [
                        index for index, item in enumerate(argv) if item == "--model"
                    ]
                    if (
                        len(model_indices) != 1
                        or model_indices[0] + 1 >= len(argv)
                        or argv[model_indices[0] + 1] != requested_model
                    ):
                        raise ValueError("CODEX_REQUEST_MODEL_ARGV_INVALID")
                    model_allowlist_sha256 = json_sha256(
                        {"allowed_models": [requested_model]}
                    )
                    request_model_binding_sha256 = json_sha256(
                        {
                            "adapter_id": "codex_cli",
                            "binary_sha256": expected_binary_sha256.upper(),
                            "cli_version": cli_version,
                            "argv_sha256": json_sha256(list(argv)),
                            "model_flag_index": model_indices[0],
                            "requested_model": requested_model,
                            "model_allowlist_sha256": model_allowlist_sha256,
                            "behavior_sha256": behavior_sha256,
                        }
                    )
                    process = self.process_transport.run(
                        argv=argv,
                        cwd=str(workspace),
                        environment=self.safe_environment(
                            adapter_id, workspace, proxy_mode, proxy_address
                        ),
                        stdin_bytes=stdin_bytes,
                        timeout_seconds=timeout_seconds,
                        shell=False,
                    )
                if not process.started:
                    raise ValueError("CLI_PROCESS_START_FAILED")
                process_launches = 1
                model_calls = 1
                if process.timed_out:
                    raise ValueError("CLI_OCR_TIMEOUT")
                if process.output_truncated:
                    raise ValueError("CLI_OUTPUT_LIMIT_EXCEEDED")
                if process.returncode != 0:
                    diagnostic = _cli_failure_diagnostic(process)
                    raise ValueError(str(diagnostic.get("category") or "CLI_OCR_FAILED"))
                execution_diagnostic, execution_error = _execution_codex_event_diagnostic(
                    process.stdout, requested_model
                )
                if execution_error is not None:
                    raise ValueError(execution_error)
                if not isinstance(execution_diagnostic, Mapping):
                    raise ValueError("CODEX_OCR_EVENT_EVIDENCE_MISSING")
                execution_diagnostic = {
                    **dict(execution_diagnostic),
                    "request_model_binding_status": (
                        "EXACT_ARGV_AND_FROZEN_ALLOWLIST"
                    ),
                    "request_model_binding_sha256": (
                        request_model_binding_sha256
                    ),
                    "model_allowlist_sha256": model_allowlist_sha256,
                }
                request_model_binding_status = str(
                    execution_diagnostic["request_model_binding_status"]
                )
                if execution_diagnostic.get("finish_status") != "SUCCESS":
                    raise ValueError("CODEX_JSONL_FINISH_NOT_SUCCESS")
                provider_identity_grade = execution_diagnostic.get(
                    "model_identity_grade"
                )
                if provider_identity_grade == "VERIFIED":
                    if execution_diagnostic.get("returned_model") != requested_model:
                        raise ValueError("CLI_RETURNED_MODEL_MISMATCH")
                    effective_identity_grade = "VERIFIED"
                    returned_model = requested_model
                elif provider_identity_grade == "PROVIDER_NOT_EXPOSED":
                    effective_identity_grade = "RECORDED_UNVERIFIED"
                    returned_model = None
                else:
                    raise ValueError("CLI_RETURNED_MODEL_CONFLICT")
                execution_usage = execution_diagnostic.get("token_usage")
                if (
                    execution_diagnostic.get("usage_status") != "ACTUAL"
                    or not isinstance(execution_usage, Mapping)
                ):
                    raise ValueError("OCR_TOKEN_EVIDENCE_MISSING")
                prompt_tokens = execution_usage.get("input_tokens")
                completion_tokens = execution_usage.get("output_tokens")
                if (
                    isinstance(prompt_tokens, bool)
                    or not isinstance(prompt_tokens, int)
                    or prompt_tokens < 0
                    or isinstance(completion_tokens, bool)
                    or not isinstance(completion_tokens, int)
                    or completion_tokens < 0
                ):
                    raise ValueError("OCR_TOKEN_EVIDENCE_MISSING")
                token_usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
                content = _codex_agent_message_from_events(process.stdout)
                finish_reason = "stop"
                raw = process.stdout
                route = "CODEX_CLI_SUBSCRIPTION_MULTIMODAL"
                region = "OPENAI_MANAGED_UNDISCLOSED"
                egress = "CODEX_CLI"
                identity_evidence = "DIRECT_CLI_REQUEST_BINDING_V1"
                estimated_cost_cny = None
                cost_evidence = "CHATGPT_SUBSCRIPTION_CLI_NO_PER_CALL_PRICE_RECEIPT"
            if not isinstance(content, str) or not content.strip():
                raise ValueError("OCR_RESPONSE_EMPTY")
            if not token_usage:
                raise ValueError("OCR_TOKEN_EVIDENCE_MISSING")
            raw_text = content
            if requested_model == "deepseek-ai/DeepSeek-OCR":
                content = _normalize_deepseek_ocr_markdown(content)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("OCR_RESPONSE_EMPTY_AFTER_NORMALIZATION")
            response_sha256 = hashlib.sha256(raw).hexdigest().upper()
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            return {
                "schema_version": "SettingsOcrImageRunnerResult-v1",
                "status": "PASS",
                "reason": "OCR_IMAGE_COMPLETED",
                "requested_model": requested_model,
                "returned_model": returned_model,
                "raw_text": raw_text,
                "text": content,
                "normalization_revision": (
                    DEEPSEEK_OCR_NORMALIZATION_REVISION
                    if requested_model == "deepseek-ai/DeepSeek-OCR"
                    else "IDENTITY"
                ),
                "normalization_applied": raw_text != content,
                "finish_reason": str(finish_reason or "unknown"),
                "duration_ms": duration_ms,
                "external_network_calls": model_calls if profile_kind == "API" else 0,
                "provider_calls": model_calls if profile_kind == "API" else 0,
                "external_model_calls": model_calls,
                "physical_model_calls": model_calls,
                "external_budget_calls": (
                    model_calls if profile_kind in {"API", "CLI"} else 0
                ),
                "local_model_calls": model_calls if profile_kind == "LOCAL" else 0,
                "external_process_launches": process_launches,
                "local_metadata_calls": metadata_calls,
                "execution_receipt": {
                    "schema_version": "SettingsOcrImageExecutionReceipt-v1",
                    "status": "PASS",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": route,
                    "region": region,
                    "egress": egress,
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "model_identity_evidence": identity_evidence,
                    "model_identity_grade": effective_identity_grade,
                    "provider_event_model_identity_grade": provider_identity_grade,
                    "request_model_binding_status": request_model_binding_status,
                    "request_model_binding_sha256": request_model_binding_sha256,
                    "model_allowlist_sha256": model_allowlist_sha256,
                    "identity_authority_ref": identity_authority_ref,
                    "cli_binary_sha256": (
                        service.get("cli_binary_sha256")
                        if profile_kind == "CLI"
                        else None
                    ),
                    "cli_version": (
                        service.get("cli_version")
                        if profile_kind == "CLI"
                        else None
                    ),
                    "behavior_sha256": behavior_sha256,
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest().upper(),
                    "response_sha256": response_sha256,
                    "raw_text_sha256": hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest().upper(),
                    "normalized_text_sha256": hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest().upper(),
                    "normalization_revision": (
                        DEEPSEEK_OCR_NORMALIZATION_REVISION
                        if requested_model == "deepseek-ai/DeepSeek-OCR"
                        else "IDENTITY"
                    ),
                    "normalization_applied": raw_text != content,
                    "finish_reason": str(finish_reason or "unknown"),
                    "latency_ms": duration_ms,
                    "token_usage": token_usage,
                    "actual_cost": None,
                    "estimated_cost_cny": estimated_cost_cny,
                    "cost_evidence": cost_evidence,
                    "max_output_tokens_mode": (
                        "PROVIDER_DEFAULT"
                        if max_output_tokens is None
                        else "PROFILE_SPECIFIC_EXPLICIT"
                    ),
                    "request_max_output_tokens": max_output_tokens,
                    "output_limit_contract": (
                        workflow_exam_output_limit_contract()
                        if purpose == "workflow_model_exam"
                        else None
                    ),
                    "observed_completion_tokens": int(
                        token_usage.get("completion_tokens") or 0
                    ),
                    "reply_length_quality_semantics": (
                        "EVIDENCE_RETAINED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
                        if purpose == "workflow_model_exam"
                        else None
                    ),
                },
            }
        except Exception as error:
            failure = self._specialized_failure(
                schema_version="SettingsOcrImageRunnerResult-v1",
                reason=str(error) or type(error).__name__,
                requested_model=requested_model if isinstance(requested_model, str) else None,
                returned_model=returned_model,
                profile_kind=profile_kind,
                started=started,
                model_calls=model_calls,
                metadata_calls=metadata_calls,
                process_launches=process_launches,
            )
            if transport_diagnostic:
                failure["transport_diagnostic"] = transport_diagnostic
            return failure
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()

    @recorded_execution
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
        started = time.monotonic()
        requested_model = model.get("model_name")
        accepted_documents = list(documents)
        if (
            profile_kind not in {"API", "LOCAL"}
            or not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not isinstance(query, str)
            or not query.strip()
            or len(query.encode("utf-8")) > 65_536
            or not accepted_documents
            or len(accepted_documents) > 64
            or any(
                not isinstance(document, str)
                or not document
                or len(document.encode("utf-8")) > 65_536
                for document in accepted_documents
            )
            or purpose not in {"workflow_model_exam", "core_document_processing"}
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 5 <= timeout_seconds <= 600
        ):
            raise ValueError("RERANK_REQUEST_INVALID")
        request_payload = {
            "model": requested_model,
            "query": query,
            "documents": accepted_documents,
            "return_documents": False,
            "top_n": len(accepted_documents),
            "max_chunks_per_doc": 1,
            "overlap_tokens": 0,
        }
        behavior_sha256 = hashlib.sha256(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        secret = ""
        secret_bytes = b""
        headers: dict[str, str] = {}
        model_calls = 0
        returned_model: str | None = requested_model if profile_kind == "LOCAL" else None
        try:
            body = json.dumps(
                request_payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if profile_kind == "API":
                profile = _api_profile(service)
                if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
                    raise ValueError("RERANK_API_PROFILE_UNSUPPORTED")
                parsed = urlsplit(profile.url)
                path = parsed.path.rstrip("/")
                if not path.casefold().endswith("/models"):
                    raise ValueError("RERANK_API_ENDPOINT_INVALID")
                endpoint = urlunsplit((parsed.scheme, parsed.netloc, path[: -len("/models")] + "/rerank", "", ""))
                credential_ref = service.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret_bytes = self._credential_resolver(credential_ref)
                if not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret = secret_bytes.decode("utf-8", errors="strict")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Memorive-Reranker-Exam/1",
                    **dict(profile.extra_headers),
                }
                headers[profile.auth_header] = (
                    f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
                )
                proxy_mode, proxy_address, _ = self.preferences()
                model_calls = 1
                response = self._http.request(
                    method="POST",
                    url=endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=timeout_seconds,
                    proxy_mode=proxy_mode,
                    proxy_address=proxy_address,
                )
                if response.status_code in {401, 403}:
                    raise ValueError("API_AUTHENTICATION_REJECTED")
                if response.status_code == 429:
                    raise ValueError("API_RATE_LIMITED")
                if response.status_code != 200:
                    raise ValueError(f"RERANK_HTTP_{response.status_code}")
                raw = response.body
                document = json.loads(raw.decode("utf-8", errors="strict"))
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model is not None and observed_model != requested_model:
                    raise ValueError("RERANK_RETURNED_MODEL_MISMATCH")
                if observed_model == requested_model:
                    returned_model = requested_model
                    identity_evidence = "PROVIDER_RESPONSE_MODEL_FIELD"
                    identity_grade = "DIRECT_RETURNED_MODEL_MATCH"
                    request_model_binding_status = "EXACT_REQUEST_AND_RESPONSE_MODEL"
                elif requested_model == "Qwen/Qwen3-VL-Reranker-8B":
                    catalog_receipt_sha256 = service.get(
                        "catalog_model_identity_receipt_sha256"
                    )
                    if (
                        service.get("catalog_model_identity_status")
                        != "AVAILABLE"
                        or not isinstance(catalog_receipt_sha256, str)
                        or not SAFE_SHA256.fullmatch(
                            catalog_receipt_sha256.upper()
                        )
                    ):
                        raise ValueError("RERANK_CATALOG_IDENTITY_EVIDENCE_MISSING")
                    returned_model = None
                    identity_evidence = (
                        "PREVALIDATED_CATALOG_AND_EXACT_REQUEST_MODEL_"
                        "PROVIDER_RESPONSE_OMITS_MODEL"
                    )
                    identity_grade = (
                        "REQUEST_BOUND_CATALOG_CONFIRMED_"
                        "PROVIDER_RESPONSE_OMITS_MODEL"
                    )
                    request_model_binding_status = (
                        "EXACT_REQUEST_MODEL_AND_PREVALIDATED_CATALOG"
                    )
                else:
                    returned_model = requested_model
                    identity_evidence = (
                        "LEGACY_EXACT_REQUEST_MODEL_PROVIDER_RESPONSE_OMITS_MODEL"
                    )
                    identity_grade = "LEGACY_REQUEST_BOUND"
                    request_model_binding_status = "EXACT_REQUEST_MODEL_ONLY"
                route = "EXISTING_SETTINGS_API_RERANK_TRANSPORT"
                region = "PROVIDER_MANAGED_UNDISCLOSED"
                egress = "PROVIDER_API"
                price = (
                    0.07
                    if requested_model == "Pro/BAAI/bge-reranker-v2-m3"
                    else 0.7
                    if requested_model == "Qwen/Qwen3-VL-Reranker-8B"
                    else 0.0
                    if requested_model == "BAAI/bge-reranker-v2-m3"
                    else None
                )
            else:
                endpoint_value = service.get("rerank_endpoint")
                if (
                    service.get("endpoint_kind") != "local_reranker"
                    or service.get("capability") != "RERANKER"
                    or service.get("execution_eligible") is not True
                    or service.get("exact_identity_available") is not True
                    or not isinstance(service.get("model_digest"), str)
                    or not SAFE_SHA256.fullmatch(str(service["model_digest"]).upper())
                    or not isinstance(endpoint_value, str)
                ):
                    raise ValueError("LOCAL_RERANK_PROFILE_INVALID")
                parsed = urlsplit(endpoint_value)
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("LOCAL_RERANK_ENDPOINT_INVALID")
                opener = build_opener(ProxyHandler({}), _NoRedirect())
                request = Request(endpoint_value, data=body, headers={"Content-Type": "application/json"}, method="POST")
                from memorive.models.calls.ledger import recording_urlopen
                opener.open = recording_urlopen(opener.open)
                model_calls = 1
                with opener.open(request, timeout=timeout_seconds) as response:
                    status_code = int(response.status)
                    raw = response.read(MAX_HTTP_BODY_BYTES + 1)
                if len(raw) > MAX_HTTP_BODY_BYTES:
                    raise ValueError("LOCAL_RERANK_RESPONSE_TOO_LARGE")
                if status_code != 200:
                    raise ValueError(f"LOCAL_RERANK_HTTP_{status_code}")
                document = json.loads(raw.decode("utf-8", errors="strict"))
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model not in {None, requested_model}:
                    raise ValueError("LOCAL_RERANK_RETURNED_MODEL_MISMATCH")
                returned_model = requested_model
                identity_evidence = "LOCAL_EXACT_PROFILE_DIGEST_AND_EXACT_REQUEST_MODEL"
                identity_grade = "LOCAL_EXACT_NAME_DIGEST_AND_REQUEST_MODEL"
                request_model_binding_status = "LOCAL_EXACT_PROFILE_BINDING"
                route = "LOCAL_RERANKER_LOOPBACK"
                region = "LOCAL_MACHINE"
                egress = "LOOPBACK_ONLY"
                price = 0.0
            results = document.get("results") if isinstance(document, Mapping) else None
            if not isinstance(results, list):
                raise ValueError("RERANK_RESULTS_MISSING")
            indices = [
                row.get("index") if isinstance(row, Mapping) else None
                for row in results
            ]
            if (
                len(results) != len(accepted_documents)
                or set(indices) != set(range(len(accepted_documents)))
            ):
                raise ValueError("RERANK_RESULT_INDEX_SET_MISMATCH")
            normalized: list[dict[str, Any]] = []
            for response_order, row in enumerate(results):
                if not isinstance(row, Mapping):
                    raise ValueError("RERANK_RESULT_INVALID")
                score = row.get("relevance_score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    raise ValueError("RERANK_SCORE_INVALID")
                normalized.append(
                    {
                        "index": int(row["index"]),
                        "relevance_score": float(score),
                        "response_order": response_order,
                    }
                )
            normalized.sort(
                key=lambda row: (
                    -row["relevance_score"],
                    row["response_order"],
                    row["index"],
                )
            )
            meta = document.get("meta") if isinstance(document.get("meta"), Mapping) else {}
            meta_tokens = meta.get("tokens") if isinstance(meta.get("tokens"), Mapping) else {}
            usage = document.get("usage") if isinstance(document.get("usage"), Mapping) else {}
            token_usage = _normalise_token_usage(usage)
            if not token_usage:
                input_tokens = _nonnegative_int(meta_tokens.get("input_tokens"))
                if input_tokens is not None:
                    token_usage = {"prompt_tokens": input_tokens, "completion_tokens": 0}
            if profile_kind == "API" and not token_usage:
                raise ValueError("RERANK_TOKEN_EVIDENCE_MISSING")
            if profile_kind == "LOCAL" and not token_usage:
                token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            estimated_cost_cny = (
                round(token_usage["prompt_tokens"] * float(price) / 1_000_000, 12)
                if price is not None
                else None
            )
            cost_evidence = (
                "FROZEN_Quality_RERANKER_INPUT_PRICE_ESTIMATE"
                if profile_kind == "API" and price is not None
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                if profile_kind == "LOCAL"
                else "ATTEMPT_COST_EVIDENCE_UNAVAILABLE"
            )
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            return {
                "schema_version": "SettingsRerankRunnerResult-v1",
                "status": "PASS",
                "reason": "RERANK_COMPLETED",
                "requested_model": requested_model,
                "returned_model": returned_model,
                "results": normalized,
                "duration_ms": duration_ms,
                "external_network_calls": model_calls if profile_kind == "API" else 0,
                "provider_calls": model_calls if profile_kind == "API" else 0,
                "external_model_calls": model_calls,
                "physical_model_calls": model_calls,
                "external_budget_calls": model_calls if profile_kind == "API" else 0,
                "local_model_calls": model_calls if profile_kind == "LOCAL" else 0,
                "external_process_launches": 0,
                "execution_receipt": {
                    "schema_version": "SettingsRerankExecutionReceipt-v1",
                    "status": "PASS",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": route,
                    "region": region,
                    "egress": egress,
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "model_identity_evidence": identity_evidence,
                    "model_identity_grade": identity_grade,
                    "request_model_binding_status": request_model_binding_status,
                    "catalog_model_identity_receipt_sha256": service.get(
                        "catalog_model_identity_receipt_sha256"
                    ),
                    "behavior_sha256": behavior_sha256,
                    "response_sha256": hashlib.sha256(raw).hexdigest().upper(),
                    "duration_ms": duration_ms,
                    "token_usage": token_usage,
            "prompt_cache": cache_usage(dict(usage)),
                    "actual_cost": None,
                    "estimated_cost_cny": estimated_cost_cny,
                    "cost_evidence": cost_evidence,
                },
            }
        except Exception as error:
            return self._specialized_failure(
                schema_version="SettingsRerankRunnerResult-v1",
                reason=str(error) or type(error).__name__,
                requested_model=requested_model if isinstance(requested_model, str) else None,
                returned_model=returned_model,
                profile_kind=profile_kind,
                started=started,
                model_calls=model_calls,
            )
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()
