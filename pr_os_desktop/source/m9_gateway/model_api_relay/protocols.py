from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .contracts import RelayContractError


@dataclass(frozen=True)
class PreparedProtocolRequest:
    path: str
    body: dict[str, Any]
    headers: dict[str, str]


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RelayContractError("REQUEST_INTEGER_INVALID", [field])
    return value


def _json_object(raw: bytes, max_bytes: int) -> dict[str, Any]:
    if len(raw) > max_bytes:
        raise RelayContractError("RESPONSE_OVERSIZE")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RelayContractError("RESPONSE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise RelayContractError("RESPONSE_OBJECT_REQUIRED")
    return value


def _nonnegative_int(value: Any, field: str, *, allow_none: bool = False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RelayContractError("USAGE_INVALID", [field])
    return value


def _usage(input_tokens: Any, output_tokens: Any, total_tokens: Any, reasoning_tokens: Any = 0):
    input_value = _nonnegative_int(input_tokens, "input_tokens")
    output_value = _nonnegative_int(output_tokens, "output_tokens")
    total_value = _nonnegative_int(total_tokens, "total_tokens")
    reasoning_value = _nonnegative_int(reasoning_tokens or 0, "reasoning_tokens")
    if total_value != input_value + output_value or reasoning_value > output_value:
        raise RelayContractError("USAGE_INVALID", ["token total or reasoning projection"])
    return {
        "input_tokens": input_value,
        "output_tokens": output_value,
        "reasoning_tokens": reasoning_value,
        "total_tokens": total_value,
    }


def _usage_or_unknown(
    input_tokens: Any,
    output_tokens: Any,
    total_tokens: Any,
    reasoning_tokens: Any = 0,
) -> tuple[dict[str, int] | None, str]:
    values = (input_tokens, output_tokens, total_tokens)
    if all(value is None for value in values):
        return None, "UNKNOWN"
    if any(value is None for value in values):
        raise RelayContractError("USAGE_PARTIAL")
    return _usage(input_tokens, output_tokens, total_tokens, reasoning_tokens), "ACTUAL_REPORTED"


def _reject_control_plane_overrides(payload: Mapping[str, Any]) -> None:
    forbidden = {
        "endpoint", "origin", "base_url", "url", "headers", "header",
        "credential", "credential_ref", "api_key", "token", "model",
        "model_id", "request_model_alias", "proxy", "proxies",
    }
    found = sorted(forbidden & set(payload))
    if found:
        raise RelayContractError("PAYLOAD_CONTROL_PLANE_OVERRIDE_FORBIDDEN", found)


def _validate_schema_shape(schema: Any, *, field: str) -> None:
    if not isinstance(schema, Mapping):
        raise RelayContractError("STRUCTURED_SCHEMA_INVALID", [field])
    allowed = {
        "$schema", "$id", "type", "properties", "required", "additionalProperties",
        "items", "enum", "description", "title", "minimum", "maximum",
    }
    if set(schema) - allowed:
        raise RelayContractError("STRUCTURED_SCHEMA_KEY_FORBIDDEN", sorted(set(schema) - allowed))
    kind = schema.get("type")
    if kind not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        raise RelayContractError("STRUCTURED_SCHEMA_TYPE_INVALID", [field])
    if kind == "object":
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise RelayContractError("STRUCTURED_SCHEMA_PROPERTIES_INVALID", [field])
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise RelayContractError("STRUCTURED_SCHEMA_REQUIRED_INVALID", [field])
        if set(required) - set(properties):
            raise RelayContractError("STRUCTURED_SCHEMA_REQUIRED_UNKNOWN", [field])
        for name, child in properties.items():
            _validate_schema_shape(child, field=f"{field}.{name}")
    if kind == "array":
        _validate_schema_shape(schema.get("items"), field=f"{field}.items")


def _validate_instance(value: Any, schema: Mapping[str, Any], *, field: str = "output") -> None:
    kind = schema.get("type")
    correct = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(kind, False)
    if not correct:
        raise RelayContractError("STRUCTURED_OUTPUT_TYPE_INVALID", [field])
    if "enum" in schema and value not in schema["enum"]:
        raise RelayContractError("STRUCTURED_OUTPUT_ENUM_INVALID", [field])
    if kind == "object":
        properties = schema.get("properties") or {}
        missing = sorted(set(schema.get("required") or []) - set(value))
        if missing:
            raise RelayContractError("STRUCTURED_OUTPUT_REQUIRED_MISSING", missing)
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise RelayContractError("STRUCTURED_OUTPUT_ADDITIONAL_PROPERTY", sorted(set(value) - set(properties)))
        for name in set(value) & set(properties):
            _validate_instance(value[name], properties[name], field=f"{field}.{name}")
    elif kind == "array":
        for index, item in enumerate(value):
            _validate_instance(item, schema["items"], field=f"{field}[{index}]")


def _structured_text(text: str, schema: Mapping[str, Any] | None) -> None:
    if schema is None:
        return
    _validate_schema_shape(schema, field="output_schema")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RelayContractError("STRUCTURED_OUTPUT_JSON_INVALID") from exc
    _validate_instance(value, schema)


def _response_meta(
    *, body_models: Sequence[str | None], response_headers: Mapping[str, str] | None
) -> dict[str, Any]:
    headers = {str(key).lower(): str(value) for key, value in (response_headers or {}).items()}
    request_id = next(
        (headers[name] for name in ("request-id", "x-request-id", "openai-request-id", "x-siliconcloud-trace-id", "x-ds-trace-id") if headers.get(name)),
        None,
    )
    header_models = [
        headers[name] for name in ("x-model-id", "x-upstream-model", "x-relay-model")
        if headers.get(name)
    ]
    return {
        "request_id": request_id,
        "response_body_models": [item for item in body_models if isinstance(item, str) and item],
        "response_header_models": header_models,
    }


def _messages(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RelayContractError("MESSAGES_INVALID")
    accepted = []
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") not in {"user", "assistant", "system"}:
            raise RelayContractError("MESSAGE_INVALID")
        if not isinstance(message.get("content"), (str, list)):
            raise RelayContractError("MESSAGE_CONTENT_INVALID")
        accepted.append(dict(message))
    return accepted


class RelayProtocolRegistry:
    def build_request(self, protocol: str, profile: Mapping[str, Any], payload: Mapping[str, Any]) -> PreparedProtocolRequest:
        if not isinstance(payload, Mapping):
            raise RelayContractError("REQUEST_PAYLOAD_NOT_OBJECT")
        _reject_control_plane_overrides(payload)
        if protocol != profile.get("protocol_family"):
            raise RelayContractError("PROTOCOL_PROFILE_MISMATCH")
        model = profile.get("request_model_alias")
        path = profile.get("path_template")
        if protocol == "OPENAI_CHAT_COMPLETIONS":
            body = {"model": model, "messages": _messages(payload), "max_tokens": _positive_int(payload.get("max_tokens", 16), "max_tokens")}
            for key in ("temperature", "response_format", "tools", "tool_choice", "stream", "thinking", "reasoning_effort"):
                if key in payload:
                    body[key] = payload[key]
            return PreparedProtocolRequest(path, body, {})
        if protocol == "OPENAI_RESPONSES":
            if "input" not in payload:
                raise RelayContractError("RESPONSES_INPUT_REQUIRED")
            body = {"model": model, "input": payload["input"], "max_output_tokens": _positive_int(payload.get("max_output_tokens", 16), "max_output_tokens")}
            for key in ("tools", "tool_choice", "text", "reasoning", "stream"):
                if key in payload:
                    body[key] = payload[key]
            for index, tool in enumerate(body.get("tools") or []):
                if not isinstance(tool, Mapping):
                    raise RelayContractError("TOOL_DECLARATION_INVALID", [str(index)])
                parameters = tool.get("parameters") or (tool.get("function") or {}).get("parameters")
                if parameters is not None:
                    _validate_schema_shape(parameters, field=f"tools[{index}].parameters")
            return PreparedProtocolRequest(path, body, {})
        if protocol == "ANTHROPIC_MESSAGES":
            messages = _messages(payload)
            system = [item for item in messages if item["role"] == "system"]
            messages = [item for item in messages if item["role"] != "system"]
            body = {"model": model, "messages": messages, "max_tokens": _positive_int(payload.get("max_tokens", 16), "max_tokens")}
            if system:
                body["system"] = "\n".join(str(item["content"]) for item in system)
            for key in ("temperature", "thinking", "output_config", "cache_control", "tools", "stream"):
                if key in payload:
                    body[key] = payload[key]
            headers = {"anthropic-version": str(payload.get("anthropic_version", "2023-06-01"))}
            if "anthropic_beta" in payload:
                headers["anthropic-beta"] = str(payload["anthropic_beta"])
            return PreparedProtocolRequest(path, body, headers)
        if protocol == "GEMINI_GENERATE_CONTENT":
            contents = payload.get("contents")
            if not isinstance(contents, list) or not contents:
                raise RelayContractError("GEMINI_CONTENTS_INVALID")
            body = {"contents": contents, "generationConfig": {"maxOutputTokens": _positive_int(payload.get("max_output_tokens", 16), "max_output_tokens")}}
            if "temperature" in payload:
                body["generationConfig"]["temperature"] = payload["temperature"]
            if "response_schema" in payload:
                _validate_schema_shape(payload["response_schema"], field="response_schema")
                body["generationConfig"].update({"responseMimeType": "application/json", "responseSchema": payload["response_schema"]})
            if "tools" in payload:
                body["tools"] = payload["tools"]
            if "system_instruction" in payload:
                body["systemInstruction"] = payload["system_instruction"]
            return PreparedProtocolRequest(path.replace("{model}", quote(str(model), safe="")), body, {})
        if protocol == "OPENAI_EMBEDDINGS":
            inputs = payload.get("input")
            if not isinstance(inputs, list) or not inputs or not all(isinstance(item, str) for item in inputs):
                raise RelayContractError("EMBEDDING_INPUT_INVALID")
            return PreparedProtocolRequest(path, {"model": model, "input": inputs}, {})
        if protocol == "PROVIDER_RERANK":
            query = payload.get("query")
            documents = payload.get("documents")
            if not isinstance(query, str) or not query or not isinstance(documents, list) or not documents or not all(isinstance(item, str) for item in documents):
                raise RelayContractError("RERANK_INPUT_INVALID")
            return PreparedProtocolRequest(path, {"model": model, "query": query, "documents": documents, "return_documents": False, "top_n": len(documents)}, {})
        raise RelayContractError("PROTOCOL_NOT_REGISTERED", [protocol])

    def parse_response(
        self,
        protocol: str,
        profile: Mapping[str, Any],
        status: int,
        content_type: str,
        raw: bytes,
        *,
        expected_count: int | None = None,
        response_headers: Mapping[str, str] | None = None,
        output_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if protocol != profile.get("protocol_family"):
            raise RelayContractError("PROTOCOL_PROFILE_MISMATCH")
        if not isinstance(status, int) or status < 200 or status >= 300:
            raise RelayContractError(f"HTTP_{status}")
        media_type = content_type.lower().split(";", 1)[0].strip() if isinstance(content_type, str) else ""
        if media_type == "text/event-stream":
            return self.parse_stream(
                protocol, profile, raw,
                response_headers=response_headers,
                output_schema=output_schema,
            )
        if media_type != "application/json":
            raise RelayContractError("CONTENT_TYPE_INVALID")
        data = _json_object(raw, int(profile.get("max_response_bytes", 1024 * 1024)))
        if protocol == "OPENAI_CHAT_COMPLETIONS":
            try:
                choice = data["choices"][0]
                text = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RelayContractError("OPENAI_CHAT_RESPONSE_INVALID") from exc
            usage = data.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            normalized, usage_status = _usage_or_unknown(usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"), details.get("reasoning_tokens", 0))
            if not isinstance(text, str):
                raise RelayContractError("OPENAI_CHAT_RESPONSE_INVALID")
            _structured_text(text, output_schema)
            return {"text": text, "returned_model": data.get("model"), "response_id": data.get("id"), "finish_reason": choice.get("finish_reason"), "usage": normalized, "usage_status": usage_status, **_response_meta(body_models=[data.get("model")], response_headers=response_headers)}
        if protocol == "OPENAI_RESPONSES":
            text = data.get("output_text")
            if not isinstance(text, str):
                pieces = []
                for item in data.get("output") or []:
                    for part in item.get("content") or []:
                        if part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                            pieces.append(part["text"])
                text = "".join(pieces)
            usage = data.get("usage") or {}
            output_details = usage.get("output_tokens_details") or {}
            normalized, usage_status = _usage_or_unknown(usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"), output_details.get("reasoning_tokens", 0))
            if not text:
                raise RelayContractError("OPENAI_RESPONSES_OUTPUT_INVALID")
            _structured_text(text, output_schema)
            return {"text": text, "returned_model": data.get("model"), "response_id": data.get("id"), "finish_reason": data.get("status"), "usage": normalized, "usage_status": usage_status, **_response_meta(body_models=[data.get("model")], response_headers=response_headers)}
        if protocol == "ANTHROPIC_MESSAGES":
            content = data.get("content")
            if not isinstance(content, list):
                raise RelayContractError("ANTHROPIC_RESPONSE_INVALID")
            text = "".join(item.get("text", "") for item in content if isinstance(item, Mapping) and item.get("type") == "text")
            usage = data.get("usage") or {}
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            normalized, usage_status = _usage_or_unknown(input_tokens, output_tokens, input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None)
            if not text:
                raise RelayContractError("ANTHROPIC_RESPONSE_INVALID")
            _structured_text(text, output_schema)
            return {"text": text, "returned_model": data.get("model"), "response_id": data.get("id"), "finish_reason": data.get("stop_reason"), "usage": normalized, "usage_status": usage_status, **_response_meta(body_models=[data.get("model")], response_headers=response_headers)}
        if protocol == "GEMINI_GENERATE_CONTENT":
            candidates = data.get("candidates") or []
            usage = data.get("usageMetadata") or {}
            if not candidates:
                block_reason = (data.get("promptFeedback") or {}).get("blockReason")
                if not block_reason:
                    raise RelayContractError("GEMINI_RESPONSE_INVALID")
                normalized, usage_status = _usage_or_unknown(usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), usage.get("totalTokenCount"), usage.get("thoughtsTokenCount", 0))
                return {"text": "", "returned_model": data.get("modelVersion"), "response_id": data.get("responseId"), "finish_reason": f"SAFETY_BLOCK:{block_reason}", "usage": normalized, "usage_status": usage_status, "safety_blocked": True, **_response_meta(body_models=[data.get("modelVersion")], response_headers=response_headers)}
            try:
                candidate = candidates[0]
                text = "".join(part.get("text", "") for part in candidate["content"]["parts"] if isinstance(part, Mapping))
            except (KeyError, IndexError, TypeError) as exc:
                raise RelayContractError("GEMINI_RESPONSE_INVALID") from exc
            normalized, usage_status = _usage_or_unknown(usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), usage.get("totalTokenCount"), usage.get("thoughtsTokenCount", 0))
            if not text:
                raise RelayContractError("GEMINI_RESPONSE_INVALID")
            _structured_text(text, output_schema)
            return {"text": text, "returned_model": data.get("modelVersion"), "response_id": data.get("responseId"), "finish_reason": candidate.get("finishReason"), "usage": normalized, "usage_status": usage_status, "safety_blocked": False, **_response_meta(body_models=[data.get("modelVersion")], response_headers=response_headers)}
        if protocol == "OPENAI_EMBEDDINGS":
            items = data.get("data")
            if not isinstance(items, list) or expected_count is None or len(items) != expected_count:
                raise RelayContractError("EMBEDDING_COUNT_INVALID")
            if [item.get("index") for item in items if isinstance(item, Mapping)] != list(range(expected_count)):
                raise RelayContractError("EMBEDDING_ORDER_INVALID")
            vectors = [item.get("embedding") for item in items]
            if not vectors or any(not isinstance(vector, list) or not vector for vector in vectors):
                raise RelayContractError("EMBEDDING_VECTOR_INVALID")
            dimension = len(vectors[0])
            if any(len(vector) != dimension or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector) for vector in vectors):
                raise RelayContractError("EMBEDDING_DIMENSION_INVALID")
            expected_dimension = profile.get("expected_embedding_dimension")
            if expected_dimension is not None and dimension != expected_dimension:
                raise RelayContractError("EMBEDDING_DIMENSION_DRIFT")
            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            total_tokens = usage.get("total_tokens")
            normalized, usage_status = _usage_or_unknown(input_tokens, 0 if input_tokens is not None or total_tokens is not None else None, total_tokens)
            return {"embeddings": vectors, "dimension": dimension, "returned_model": data.get("model"), "response_id": data.get("id"), "usage": normalized, "usage_status": usage_status, **_response_meta(body_models=[data.get("model")], response_headers=response_headers)}
        if protocol == "PROVIDER_RERANK":
            items = data.get("results")
            if not isinstance(items, list) or expected_count is None or len(items) != expected_count:
                raise RelayContractError("RERANK_COUNT_INVALID")
            if sorted(item.get("index") for item in items if isinstance(item, Mapping)) != list(range(expected_count)):
                raise RelayContractError("RERANK_ORDER_INVALID")
            for item in items:
                score = item.get("relevance_score", item.get("score"))
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise RelayContractError("RERANK_SCORE_INVALID")
                score_range = profile.get("rerank_score_range")
                if score_range is not None and not score_range[0] <= score <= score_range[1]:
                    raise RelayContractError("RERANK_SCORE_OUT_OF_RANGE")
            items = sorted(items, key=lambda item: item["index"])
            usage = data.get("usage") or {}
            if not usage:
                meta_tokens = (data.get("meta") or {}).get("tokens") or {}
                if meta_tokens:
                    input_tokens = meta_tokens.get("input_tokens")
                    output_tokens = meta_tokens.get("output_tokens", 0)
                    usage = {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens
                        if isinstance(input_tokens, int) and isinstance(output_tokens, int)
                        else None,
                    }
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
            if output_tokens is None and (input_tokens is not None or usage.get("total_tokens") is not None):
                output_tokens = 0
            normalized, usage_status = _usage_or_unknown(input_tokens, output_tokens, usage.get("total_tokens"))
            return {"results": items, "returned_model": data.get("model"), "response_id": data.get("id"), "usage": normalized, "usage_status": usage_status, **_response_meta(body_models=[data.get("model")], response_headers=response_headers)}
        raise RelayContractError("PROTOCOL_NOT_REGISTERED", [protocol])

    def parse_stream(
        self,
        protocol: str,
        profile: Mapping[str, Any],
        raw: bytes,
        *,
        response_headers: Mapping[str, str] | None = None,
        output_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if protocol != profile.get("protocol_family"):
            raise RelayContractError("PROTOCOL_PROFILE_MISMATCH")
        if len(raw) > int(profile.get("max_response_bytes", 1024 * 1024)):
            raise RelayContractError("RESPONSE_OVERSIZE")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RelayContractError("STREAM_UTF8_INVALID") from exc
        events = []
        for block in text.replace("\r\n", "\n").split("\n\n"):
            if not block.strip():
                continue
            event_type = None
            data_lines = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line.startswith(":"):
                    continue
                else:
                    raise RelayContractError("STREAM_LINE_INVALID")
            if not data_lines:
                continue
            data_text = "\n".join(data_lines)
            if data_text == "[DONE]":
                events.append((event_type or "done", "[DONE]"))
                continue
            try:
                value = json.loads(data_text)
            except json.JSONDecodeError as exc:
                raise RelayContractError("STREAM_JSON_INVALID") from exc
            if not isinstance(value, dict):
                raise RelayContractError("STREAM_EVENT_OBJECT_REQUIRED")
            events.append((event_type or value.get("type") or "message", value))
        if not events:
            raise RelayContractError("STREAM_EMPTY")
        if protocol == "OPENAI_CHAT_COMPLETIONS":
            pieces = []
            models = set()
            usage = None
            finish_reason = None
            done = False
            for _kind, event in events:
                if event == "[DONE]":
                    if done:
                        raise RelayContractError("OPENAI_STREAM_DUPLICATE_TERMINAL")
                    done = True
                    continue
                if done:
                    raise RelayContractError("OPENAI_STREAM_EVENT_AFTER_TERMINAL")
                if event.get("model"):
                    models.add(event["model"])
                if event.get("usage") is not None:
                    usage = event["usage"]
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        pieces.append(delta["content"])
                    if choices[0].get("finish_reason") is not None:
                        finish_reason = choices[0]["finish_reason"]
            if not done or len(models) > 1 or finish_reason is None:
                raise RelayContractError("OPENAI_STREAM_INCOMPLETE_OR_CONFLICT")
            usage = usage or {}
            details = usage.get("completion_tokens_details") or {}
            normalized, usage_status = _usage_or_unknown(usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"), details.get("reasoning_tokens", 0))
            text_value = "".join(pieces)
            _structured_text(text_value, output_schema)
            model = next(iter(models), None)
            return {"text": text_value, "returned_model": model, "finish_reason": finish_reason, "usage": normalized, "usage_status": usage_status, "response_event_models": list(models), **_response_meta(body_models=[model], response_headers=response_headers)}
        if protocol == "ANTHROPIC_MESSAGES":
            pieces = []
            model = None
            input_tokens = None
            output_tokens = None
            finish_reason = None
            started = stopped = False
            for kind, event in events:
                event_type = event.get("type", kind)
                if stopped:
                    raise RelayContractError("ANTHROPIC_STREAM_EVENT_AFTER_TERMINAL")
                if event_type == "message_start":
                    if started:
                        raise RelayContractError("ANTHROPIC_STREAM_DUPLICATE_START")
                    started = True
                    message = event.get("message") or {}
                    model = message.get("model")
                    input_tokens = (message.get("usage") or {}).get("input_tokens")
                elif event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        pieces.append(delta["text"])
                elif event_type == "message_delta":
                    finish_reason = (event.get("delta") or {}).get("stop_reason")
                    output_tokens = (event.get("usage") or {}).get("output_tokens")
                elif event_type == "message_stop":
                    stopped = True
            if not started or not stopped:
                raise RelayContractError("ANTHROPIC_STREAM_INCOMPLETE")
            normalized, usage_status = _usage_or_unknown(input_tokens, output_tokens, input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None)
            text_value = "".join(pieces)
            _structured_text(text_value, output_schema)
            return {"text": text_value, "returned_model": model, "finish_reason": finish_reason, "usage": normalized, "usage_status": usage_status, "response_event_models": [model] if model else [], **_response_meta(body_models=[model], response_headers=response_headers)}
        if protocol == "OPENAI_RESPONSES":
            pieces = []
            terminal = None
            for kind, event in events:
                event_type = event.get("type", kind)
                if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
                    if terminal is not None:
                        raise RelayContractError("RESPONSES_STREAM_EVENT_AFTER_TERMINAL")
                    pieces.append(event["delta"])
                elif event_type == "response.completed":
                    if terminal is not None:
                        raise RelayContractError("RESPONSES_STREAM_DUPLICATE_TERMINAL")
                    terminal = event.get("response") or {}
            if terminal is None:
                raise RelayContractError("RESPONSES_STREAM_INCOMPLETE")
            usage = terminal.get("usage") or {}
            output_details = usage.get("output_tokens_details") or {}
            normalized, usage_status = _usage_or_unknown(usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"), output_details.get("reasoning_tokens", 0))
            text_value = "".join(pieces) or terminal.get("output_text", "")
            if not isinstance(text_value, str) or not text_value:
                raise RelayContractError("RESPONSES_STREAM_OUTPUT_INVALID")
            _structured_text(text_value, output_schema)
            model = terminal.get("model")
            return {"text": text_value, "returned_model": model, "finish_reason": terminal.get("status"), "usage": normalized, "usage_status": usage_status, "response_event_models": [model] if model else [], **_response_meta(body_models=[model], response_headers=response_headers)}
        if protocol == "GEMINI_GENERATE_CONTENT":
            pieces = []
            model = None
            finish_reason = None
            usage = None
            terminal_count = 0
            for _kind, event in events:
                model = event.get("modelVersion", model)
                candidates = event.get("candidates") or []
                if candidates:
                    event_finish = candidates[0].get("finishReason")
                    if event_finish is not None:
                        terminal_count += 1
                        finish_reason = event_finish
                    for part in (candidates[0].get("content") or {}).get("parts") or []:
                        if isinstance(part.get("text"), str):
                            pieces.append(part["text"])
                if event.get("usageMetadata"):
                    usage = event["usageMetadata"]
            if finish_reason is None or terminal_count != 1:
                raise RelayContractError("GEMINI_STREAM_INCOMPLETE")
            usage = usage or {}
            normalized, usage_status = _usage_or_unknown(usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), usage.get("totalTokenCount"), usage.get("thoughtsTokenCount", 0))
            text_value = "".join(pieces)
            _structured_text(text_value, output_schema)
            return {"text": text_value, "returned_model": model, "finish_reason": finish_reason, "usage": normalized, "usage_status": usage_status, "response_event_models": [model] if model else [], **_response_meta(body_models=[model], response_headers=response_headers)}
        raise RelayContractError("STREAMING_NOT_SUPPORTED_FOR_PROTOCOL", [protocol])
