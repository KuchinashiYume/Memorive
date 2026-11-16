"""Structured answers: the prompt that asks for a JSON object matching a schema, and finding that
object in a model's reply.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping


def _structured_prompt(prompt: str, response_schema: Mapping[str, Any]) -> bytes:
    schema_text = json.dumps(
        response_schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    value = (
        f"{prompt.rstrip()}\n\n"
        "Return exactly one JSON object. Do not wrap it in Markdown. "
        "The object must conform to this JSON Schema:\n"
        f"{schema_text}"
    ).encode("utf-8")
    if not value:
        raise ValueError("STRUCTURED_CHAT_PROMPT_EMPTY")
    return value


def _json_object_from_text(value: str) -> Mapping[str, Any] | None:
    accepted = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", accepted, re.DOTALL | re.IGNORECASE)
    if fenced:
        accepted = fenced.group(1).strip()
    try:
        document = json.loads(accepted)
    except json.JSONDecodeError:
        start = accepted.find("{")
        if start < 0:
            return None
        try:
            document, _ = json.JSONDecoder().raw_decode(accepted[start:])
        except json.JSONDecodeError:
            return None
    return document if isinstance(document, Mapping) else None


def _walk_structured_candidates(value: Any, found: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        if isinstance(value.get("schema_version"), str) and isinstance(value.get("items"), list):
            found.append(value)
        for key, child in value.items():
            if str(key).casefold() in {
                "text", "content", "result", "response", "output", "output_text", "message"
            } and isinstance(child, str):
                parsed = _json_object_from_text(child)
                if parsed is not None:
                    found.append(parsed)
            _walk_structured_candidates(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_structured_candidates(child, found)


def _structured_document_from_output(output: bytes) -> Mapping[str, Any] | None:
    found: list[Mapping[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _walk_structured_candidates(value, found)
    if not found:
        try:
            text_value = output.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        direct = _json_object_from_text(text_value)
        if direct is not None:
            found.append(direct)
        else:
            try:
                value = json.loads(text_value)
            except json.JSONDecodeError:
                value = None
            _walk_structured_candidates(value, found)
    return found[-1] if found else None
