"""The API route of a chat: the chat URL and request body for the profile's protocol, reading the
reply (Anthropic and OpenAI-compatible streams included), and the evidence kept when a call fails.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit
from memorive.models.endpoints import SAFE_MODEL, _ApiProfile
from model_gateway.resource_limits import _registry_entry

from memorive.models.validation.structured import _json_object_from_text, _structured_prompt


def _api_chat_url(profile: _ApiProfile, model: str) -> str:
    parsed = urlsplit(profile.url)
    path = parsed.path.rstrip("/")
    if not path.casefold().endswith("/models"):
        raise ValueError("API_CHAT_ENDPOINT_INVALID")
    root = path[: -len("/models")]
    if profile.provider_family == "ANTHROPIC":
        chat_path = f"{root}/messages"
    elif profile.provider_family == "GEMINI":
        chat_path = f"{root}/models/{quote(model, safe='._-')}:generateContent"
    else:
        chat_path = f"{root}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, chat_path, "", ""))


def _api_chat_request(
    profile: _ApiProfile,
    *,
    model: str,
    prompt: str,
    response_schema: Mapping[str, Any],
    max_output_tokens: int | None,
    model_config: Mapping[str, Any],
) -> bytes:
    prompt_text = _structured_prompt(prompt, response_schema).decode("utf-8")
    from memorive.models.request_options import reasoning_profile, resolve_reasoning_options, apply_reasoning_options
    reasoning_control = reasoning_profile(model, profile.url, profile.provider_family)
    reasoning_options = resolve_reasoning_options(reasoning_control, model_config)
    if profile.provider_family == "ANTHROPIC":
        if max_output_tokens is None:
            raise ValueError("API_OUTPUT_CAPACITY_REQUIRED")
        value = {
            "model": model,
            "max_tokens": max_output_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        from memorive.models.request_options import anthropic_options
        if reasoning_options is None:
            value.update(anthropic_options(model, model_config, max_output_tokens))
        if model_config.get("streaming") is True:
            value["stream"] = True
    elif profile.provider_family == "GEMINI":
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
        }
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        value = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": generation_config,
        }
    else:
        value = {
            "model": model,
            "stream": bool(model_config.get("streaming", _registry_entry(model)[0].get("capabilities", {}).get("streaming", False))),
            "messages": [{"role": "user", "content": prompt_text}],
            "response_format": {"type": "json_object"},
        }
        api_host = (urlsplit(profile.url).hostname or "").casefold()
        if (
            api_host == "dashscope.aliyuncs.com"
            and model.startswith("qwen3.8-")
            and isinstance(model_config.get("thinking"), bool)
        ):
            value["enable_thinking"] = model_config["thinking"]
        if model_config.get("streaming") is True:
            value["stream"] = True
            value["stream_options"] = {"include_usage": True}
        if max_output_tokens is not None:
            output_field = model_config.get(
                "max_output_tokens_wire_field", "max_tokens"
            )
            if reasoning_options is not None:
                output_field = reasoning_control['output_field']
            if output_field not in {"max_tokens", "max_completion_tokens"}:
                raise ValueError("API_MAX_OUTPUT_TOKENS_FIELD_INVALID")
            value[output_field] = max_output_tokens
        if (
            reasoning_options is None
            and model_config.get("thinking") is True
            and model_config.get("reasoning_effort") is None
        ):
            value["thinking"] = {"type": "enabled"}
        reasoning_effort = model_config.get("reasoning_effort")
        if reasoning_options is None and reasoning_effort is not None:
            if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
                raise ValueError("API_REASONING_EFFORT_INVALID")
            value["reasoning_effort"] = reasoning_effort
    if reasoning_options is not None:
        apply_reasoning_options(value, reasoning_options)
        if reasoning_control.get('streaming') and profile.provider_family != 'GEMINI':
            value['stream'] = True
            if profile.provider_family == 'OPENAI_COMPATIBLE':
                value['stream_options'] = {'include_usage': True}
    if model_config.get('prompt_cache_layout')=='MEMO_RESEARCH_PREFIX_V1' and profile.provider_family=='ANTHROPIC' and urlsplit(profile.url).hostname=='api.anthropic.com':
        from model_gateway.research_prompt_cache import prefix_parts
        parts=prefix_parts(prompt_text)
        if parts and len(parts[0])>=2048:
            value['messages'][0]['content']=[{'type':'text','text':parts[0],'cache_control':{'type':'ephemeral','ttl':'5m'}},{'type':'text','text':parts[1]}]
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _api_chat_response(
    profile: _ApiProfile,
    body: bytes,
    model_config: Mapping[str, Any] | None = None,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if (
        profile.provider_family == "ANTHROPIC"
        and body.lstrip().startswith((b"event:", b"data:"))
    ):
        return _anthropic_stream_response(body)
    if (
        profile.provider_family == "OPENAI_COMPATIBLE"
        and body.lstrip().startswith((b"event:", b"data:"))
    ):
        return _openai_compatible_stream_response(body)
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID") from error
    if not isinstance(value, Mapping):
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    usage = value.get("usage") if isinstance(value.get("usage"), Mapping) else {}
    returned_model = value.get("model") or value.get("modelVersion")
    text_value: Any = None
    finish_reason = None
    if profile.provider_family == "ANTHROPIC":
        finish_reason = value.get('stop_reason')
        content = value.get("content")
        if isinstance(content, list):
            text_value = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, Mapping) and isinstance(item.get("text"), str)
            )
    elif profile.provider_family == "GEMINI":
        candidates = value.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
            finish_reason = candidates[0].get('finishReason')
            content = candidates[0].get("content")
            parts = content.get("parts") if isinstance(content, Mapping) else None
            if isinstance(parts, list):
                text_value = "".join(
                    item.get("text", "")
                    for item in parts
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str)
                )
        usage = (
            value.get("usageMetadata")
            if isinstance(value.get("usageMetadata"), Mapping)
            else usage
        )
    else:
        choices = value.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            finish_reason = choices[0].get("finish_reason")
            message = choices[0].get("message")
            if isinstance(message, Mapping):
                text_value = message.get("content")
    if not isinstance(returned_model, str) or not returned_model:
        raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISSING")
    if finish_reason in {"length", "max_tokens", "MAX_TOKENS"}:
        raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
    if not isinstance(text_value, str) or not text_value.strip():
        raise ValueError("STRUCTURED_CHAT_RESPONSE_EMPTY")
    document = _json_object_from_text(text_value)
    if document is None:
        if profile.provider_family == "OPENAI_COMPATIBLE" and finish_reason == "length":
            raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, document, usage


def _anthropic_stream_projection(
    body: bytes,
) -> tuple[str | None, str, Mapping[str, Any], str | None, bool]:
    returned_model: str | None = None
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    stop_reason: str | None = None
    message_stop_seen = False
    event_count = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[len(b"data:") :].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            event = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID") from error
        if not isinstance(event, Mapping):
            raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
        event_count += 1
        event_type = event.get("type")
        if event_type == "error":
            raise ValueError("API_STRUCTURED_CHAT_REQUEST_FAILED")
        if event_type == "message_start":
            message = event.get("message")
            if isinstance(message, Mapping):
                candidate_model = message.get("model")
                if isinstance(candidate_model, str) and candidate_model:
                    returned_model = candidate_model
                initial_usage = message.get("usage")
                if isinstance(initial_usage, Mapping):
                    usage.update(initial_usage)
            continue
        if event_type == "content_block_start":
            content_block = event.get("content_block")
            if (
                isinstance(content_block, Mapping)
                and content_block.get("type") == "text"
                and isinstance(content_block.get("text"), str)
            ):
                text_parts.append(str(content_block["text"]))
            continue
        if event_type == "content_block_delta":
            delta = event.get("delta")
            if (
                isinstance(delta, Mapping)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("text"), str)
            ):
                text_parts.append(str(delta["text"]))
            continue
        if event_type == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping) and isinstance(
                delta.get("stop_reason"), str
            ):
                stop_reason = str(delta["stop_reason"])
            final_usage = event.get("usage")
            if isinstance(final_usage, Mapping):
                usage.update(final_usage)
            continue
        if event_type == "message_stop":
            message_stop_seen = True
    if event_count == 0:
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, "".join(text_parts), usage, stop_reason, message_stop_seen


def _anthropic_stream_response(
    body: bytes,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    returned_model, text_value, usage, stop_reason, message_stop_seen = (
        _anthropic_stream_projection(body)
    )
    if not isinstance(returned_model, str) or not returned_model:
        raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISSING")
    if not message_stop_seen:
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    if stop_reason in {"length", "max_tokens"}:
        raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
    if not text_value.strip():
        raise ValueError("STRUCTURED_CHAT_RESPONSE_EMPTY")
    document = _json_object_from_text(text_value)
    if document is None:
        if stop_reason == "max_tokens":
            raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, document, usage


def _openai_compatible_stream_response(
    body: bytes,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    returned_model: str | None = None
    text_parts: list[str] = []
    usage: Mapping[str, Any] = {}
    finish_reason: str | None = None
    event_count = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(b":"):
            continue
        if not line.startswith(b"data:"):
            continue
        payload = line[len(b"data:") :].strip()
        if payload == b"[DONE]":
            continue
        try:
            value = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("STRUCTURED_CHAT_STREAM_RESPONSE_INVALID") from error
        if not isinstance(value, Mapping):
            raise ValueError("STRUCTURED_CHAT_STREAM_RESPONSE_INVALID")
        event_count += 1
        model_value = value.get("model")
        if isinstance(model_value, str) and model_value:
            if returned_model is not None and returned_model != model_value:
                raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISMATCH")
            returned_model = model_value
        if isinstance(value.get("usage"), Mapping):
            usage = value["usage"]
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0]
        if not isinstance(first, Mapping):
            continue
        if isinstance(first.get("finish_reason"), str):
            finish_reason = str(first["finish_reason"])
        delta = first.get("delta")
        if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
            text_parts.append(str(delta["content"]))
        message = first.get("message")
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            text_parts.append(str(message["content"]))
    if event_count == 0:
        raise ValueError("STRUCTURED_CHAT_STREAM_RESPONSE_INVALID")
    if returned_model is None:
        raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISSING")
    text_value = "".join(text_parts)
    if not text_value.strip():
        raise ValueError("STRUCTURED_CHAT_RESPONSE_EMPTY")
    document = _json_object_from_text(text_value)
    if document is None:
        if finish_reason == "length":
            raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, document, usage


def _api_failure_evidence(
    body: bytes,
) -> tuple[str | None, Mapping[str, Any], Mapping[str, Any]]:
    body_sha256 = hashlib.sha256(body).hexdigest().upper()
    if b"data:" in body:
        try:
            returned, text, usage, stop_reason, message_stop_seen = (
                _anthropic_stream_projection(body)
            )
        except ValueError:
            returned, text, usage, stop_reason, message_stop_seen = (
                None,
                "",
                {},
                None,
                False,
            )
        diagnostic: dict[str, Any] = {
            "provider_body_sha256": body_sha256,
            "provider_body_json": False,
            "provider_body_sse": True,
            "message_stop_seen": message_stop_seen,
        }
        if isinstance(stop_reason, str) and len(stop_reason) <= 80:
            diagnostic["finish_reason"] = stop_reason
        if text:
            diagnostic["content_chars"] = len(text)
            diagnostic["content_sha256"] = hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest().upper()
        return returned, usage, diagnostic
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, {}, {
            "provider_body_sha256": body_sha256,
            "provider_body_json": False,
        }
    if not isinstance(value, Mapping):
        return None, {}, {
            "provider_body_sha256": body_sha256,
            "provider_body_json": True,
        }
    returned = value.get("model") or value.get("modelVersion")
    if not isinstance(returned, str) or not SAFE_MODEL.fullmatch(returned):
        returned = None
    usage = value.get("usage")
    if not isinstance(usage, Mapping):
        usage = value.get("usageMetadata")
    diagnostic: dict[str, Any] = {
        "provider_body_sha256": body_sha256,
        "provider_body_json": True,
    }
    from memorive.models.request_options import safe_error_details
    diagnostic.update(safe_error_details(value))
    stop_reason = value.get('stop_reason')
    candidates = value.get('candidates')
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
        stop_reason = candidates[0].get('finishReason')
    if stop_reason in {'max_tokens', 'MAX_TOKENS'}:
        diagnostic['finish_reason'] = 'length'
        diagnostic['provider_stop_reason'] = stop_reason
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        finish_reason = choices[0].get("finish_reason")
        if isinstance(finish_reason, str) and len(finish_reason) <= 80:
            diagnostic["finish_reason"] = finish_reason
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, str):
            diagnostic["content_chars"] = len(content)
            diagnostic["content_sha256"] = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest().upper()
    return returned, usage if isinstance(usage, Mapping) else {}, diagnostic
