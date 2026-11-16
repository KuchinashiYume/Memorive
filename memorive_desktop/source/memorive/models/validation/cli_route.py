"""The command-line route of a chat: the argument list for each supported tool, the JSON schema
Codex is given, reading Codex's event stream, the diagnostics kept when a run fails, and the hash of
the executable file that ran.
"""
from __future__ import annotations

from dataclasses import field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from memorive.models.endpoints import SAFE_MODEL, SAFE_THINKING
try:
    from model_gateway.execution_core.adapters.codex_cli import (
        CodexAdapterViolation as _ExecutionCodexAdapterViolation,
        CodexCliAdapter as _ExecutionCodexCliAdapter,
    )
except ImportError:  # optional in source-only service tests; packaged EXE includes it
    _ExecutionCodexAdapterViolation = None  # type: ignore[assignment]
    _ExecutionCodexCliAdapter = None

from memorive.models.validation.transport import ProcessResult


MINIMAL_PROMPT = b"Reply with OK only. Do not use tools."


_CODEX_TOOL_DISABLE_CONFIG = (
    "features.shell_tool=false",
    "features.code_mode=false",
    "features.browser_use=false",
    "features.computer_use=false",
    "features.image_generation=false",
    "features.workspace_dependencies=false",
    "features.multi_agent=false",
)


_CODEX_TRANSPORT_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "anyOf",
        "$ref",
        "$defs",
        "definitions",
        "description",
        "title",
    }
)


_CLAUDE_DISALLOWED_TOOLS = (
    "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,"
    "Computer,NotebookEdit,AskUserQuestion"
)


def _json_schema_scalar_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    raise ValueError("CODEX_TRANSPORT_SCHEMA_VALUE_INVALID")


def _codex_refinement_transport_schema(source: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use a homogeneous transport union; preserve ordered local validation."""
    candidate = json.loads(json.dumps(source))
    items = candidate.get("properties", {}).get("items")
    if not isinstance(items, dict) or not isinstance(items.get("prefixItems"), list) or not items["prefixItems"]:
        raise ValueError("CODEX_REFINEMENT_TUPLE_SCHEMA_INVALID")
    # prefixItems is unsupported by Codex. Each alternative retains its exact
    # message identity; the original schema and refinement validator still
    # enforce count, order, string lengths and provenance after the response.
    items["items"] = {"anyOf": items.pop("prefixItems")}
    projected, diagnostic = _codex_transport_schema(candidate)
    diagnostic["authoritative_source_schema_sha256"] = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()
    diagnostic["refinement_tuple_projection"] = "ITEM_UNION_PLUS_ORIGINAL_ORDER_VALIDATOR"
    return projected, diagnostic


def _codex_transport_schema(
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project a Quality schema onto Codex structured-output's strict subset.

    The source schema remains the authoritative post-response validator.  This
    projection only removes constraints that the Codex/OpenAI transport rejects
    (for example ``uniqueItems`` and ``minLength``), while retaining the same
    object/array/scalar shape and closing every object.
    """

    removed: dict[str, int] = {}
    inferred_types = 0
    completed_required = 0

    def walk(schema: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal inferred_types, completed_required
        projected: dict[str, Any] = {}
        for key, value in schema.items():
            if key not in _CODEX_TRANSPORT_SCHEMA_KEYS:
                removed[key] = removed.get(key, 0) + 1
                continue
            if key in {"properties", "$defs", "definitions"}:
                if not isinstance(value, Mapping):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_MAPPING_INVALID")
                projected[key] = {
                    str(name): walk(child)
                    for name, child in value.items()
                    if isinstance(name, str) and isinstance(child, Mapping)
                }
                if len(projected[key]) != len(value):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_MAPPING_INVALID")
            elif key == "items":
                if not isinstance(value, Mapping):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_ITEMS_INVALID")
                projected[key] = walk(value)
            elif key == "anyOf":
                if (
                    not isinstance(value, list)
                    or not value
                    or any(not isinstance(item, Mapping) for item in value)
                ):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_ANYOF_INVALID")
                projected[key] = [walk(item) for item in value]
            elif key in {"required", "enum", "type"}:
                projected[key] = json.loads(json.dumps(value))
            elif key == "additionalProperties":
                if value is not False:
                    raise ValueError(
                        "CODEX_TRANSPORT_SCHEMA_OPEN_OBJECT_FORBIDDEN"
                    )
                projected[key] = False
            else:
                projected[key] = json.loads(json.dumps(value))

        properties = projected.get("properties")
        if isinstance(properties, Mapping):
            observed_type = projected.get("type")
            if observed_type is not None and observed_type != "object":
                raise ValueError("CODEX_TRANSPORT_SCHEMA_OBJECT_TYPE_CONFLICT")
            if "type" not in projected:
                projected["type"] = "object"
                inferred_types += 1
            required = list(properties)
            if projected.get("required") != required:
                completed_required += 1
            projected["required"] = required
            projected["additionalProperties"] = False
        if "items" in projected and "type" not in projected:
            projected["type"] = "array"
            inferred_types += 1
        if projected.get("type") == "array" and "items" not in projected:
            raise ValueError("CODEX_TRANSPORT_SCHEMA_ARRAY_ITEMS_REQUIRED")
        if "const" in projected and "type" not in projected:
            projected["type"] = _json_schema_scalar_type(projected["const"])
            inferred_types += 1
        if "enum" in projected and "type" not in projected:
            enum = projected["enum"]
            if not isinstance(enum, list) or not enum:
                raise ValueError("CODEX_TRANSPORT_SCHEMA_ENUM_INVALID")
            observed = {_json_schema_scalar_type(item) for item in enum}
            if len(observed) != 1:
                raise ValueError("CODEX_TRANSPORT_SCHEMA_ENUM_TYPE_AMBIGUOUS")
            projected["type"] = next(iter(observed))
            inferred_types += 1
        return projected

    projected = walk(source)
    source_hash = hashlib.sha256(
        json.dumps(
            source,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    projected_hash = hashlib.sha256(
        json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    return projected, {
        "codex_transport_schema_projection": "OPENAI_STRICT_SUBSET_V1",
        "source_schema_sha256": source_hash,
        "transport_schema_sha256": projected_hash,
        "removed_keyword_counts": dict(sorted(removed.items())),
        "inferred_type_count": inferred_types,
        "completed_required_count": completed_required,
        "source_schema_remains_post_response_authority": True,
    }


def _cli_argv(
    adapter_id: str,
    executable: str,
    model: str,
    thinking_mode: str,
    cwd: str,
    prompt_bytes: bytes = MINIMAL_PROMPT,
    output_schema_path: str | None = None,
    image_path: str | None = None,
) -> tuple[tuple[str, ...], bytes | None]:
    from memorive.models.cli.windows import native_process_path
    cwd=native_process_path(cwd)
    if output_schema_path:
        output_schema_path=native_process_path(output_schema_path)
    if image_path:
        image_path=native_process_path(image_path)
    if not SAFE_MODEL.fullmatch(model):
        raise ValueError("CLI_MODEL_NAME_INVALID")
    if thinking_mode and not SAFE_THINKING.fullmatch(thinking_mode):
        raise ValueError("CLI_THINKING_MODE_INVALID")
    if adapter_id == "codex_cli":
        # ChatGPT subscription auth belongs to Codex's built-in provider.  A
        # synthetic provider without a base URL makes an otherwise valid CLI
        # login fail before the requested model can run.
        config = _CODEX_TOOL_DISABLE_CONFIG
        config_argv = tuple(part for value in config for part in ("--config", value))
        effort = (
            ("--config", f'model_reasoning_effort="{thinking_mode}"')
            if thinking_mode
            else ()
        )
        output_schema = (
            ("--output-schema", output_schema_path)
            if output_schema_path is not None
            else ()
        )
        image = ("--image", image_path) if image_path is not None else ()
        return (
            (
                executable,
                "exec",
                "--ignore-user-config",
                "--strict-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--sandbox",
                "read-only",
                "--model",
                model,
                *effort,
                "--config",
                'shell_environment_policy.inherit="none"',
                *config_argv,
                "--cd",
                cwd,
                "--json",
                *output_schema,
                *image,
                "-",
            ),
            prompt_bytes,
        )
    if adapter_id == "claude_code":
        effort = ("--effort", thinking_mode) if thinking_mode else ()
        return (
            (
                executable,
                "--print",
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--verbose",
                "--model",
                model,
                *effort,
                "--permission-mode",
                "plan",
                "--tools",
                "",
                "--disallowedTools",
                _CLAUDE_DISALLOWED_TOOLS,
                "--safe-mode",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--mcp-config",
                "{}",
                "--no-chrome",
                "--no-session-persistence",
                "--prompt-suggestions",
                "false",
            ),
            prompt_bytes,
        )
    if adapter_id == "gemini_cli":
        return (
            (
                executable,
                "--prompt",
                "",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--approval-mode",
                "plan",
                "--extensions",
                "",
            ),
            prompt_bytes,
        )
    if adapter_id == "qwen_code":
        return (
            (
                executable,
                "--prompt",
                "",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--safe-mode",
                "--sandbox",
            ),
            prompt_bytes,
        )
    if adapter_id == "kimi_code":
        return (
            (
                executable,
                "--prompt",
                "",
                "--output-format",
                "stream-json",
                "--model",
                model,
            ),
            prompt_bytes,
        )
    if adapter_id == "codebuddy_code":
        return (
            (
                executable,
                "-p",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--strict-mcp-config",
                "--no-session-persistence",
            ),
            prompt_bytes,
        )
    if adapter_id == "github_copilot_cli":
        return (
            (
                executable,
                "-p",
                prompt_bytes.decode("utf-8", errors="strict"),
                "--output-format=json",
                f"--model={model}",
                "--plan",
                "--deny-tool=shell,write,read,url,memory,github",
                "--disable-builtin-mcps",
                "--no-custom-instructions",
                "--no-experimental",
                "--no-remote",
                "--no-remote-export",
                "--no-auto-update",
                "--no-ask-user",
                "--no-color",
                "--stream=off",
                "--secret-env-vars=COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN",
            ),
            None,
        )
    raise ValueError("CLI_ADAPTER_UNSUPPORTED")


def _cli_failure_diagnostic(process: ProcessResult) -> Mapping[str, Any]:
    text = process.stderr.decode("utf-8", errors="replace").casefold()
    stdout_text = process.stdout.decode("utf-8", errors="replace").casefold()
    combined = text + "\n" + stdout_text
    capacity = {}
    if "input_too_large" in combined:
        for field in ("max_chars", "actual_chars"):
            match = re.search(r'"' + field + r'"\s*:\s*(\d{1,12})\b', combined)
            if match:
                capacity[field] = int(match.group(1))
        capacity["source"] = "CLI_ERROR_RESPONSE"
    if capacity:
        category = "CODEX_INPUT_CAPACITY_EXCEEDED"
    elif "invalid_json_schema" in combined or "invalid schema for response_format" in combined:
        category = "CODEX_OUTPUT_SCHEMA_REJECTED"
    elif "request timed out" in combined:
        category = "CODEX_TRANSPORT_TIMEOUT"
    elif "model provider" in text and ("base url" in text or "base_url" in text):
        category = "CODEX_CUSTOM_PROVIDER_BASE_URL_MISSING"
    elif "config" in text and any(
        marker in text for marker in ("unknown", "unrecognized", "invalid")
    ):
        category = "CODEX_CONFIG_INVALID"
    elif any(marker in text for marker in ("not logged in", "login required", "unauthorized")):
        category = "CODEX_AUTH_UNAVAILABLE"
    elif "model" in text and any(
        marker in text for marker in ("not found", "not available", "unsupported")
    ):
        category = "CODEX_MODEL_UNAVAILABLE"
    else:
        category = "CODEX_PROCESS_NONZERO"
    return {
        "category": category,
        "observed_input_capacity": capacity or None,
        "returncode": process.returncode,
        "stdout_sha256": hashlib.sha256(process.stdout).hexdigest().upper(),
        "stderr_sha256": hashlib.sha256(process.stderr).hexdigest().upper(),
        "stdout_total_bytes": process.stdout_total_bytes,
        "stderr_total_bytes": process.stderr_total_bytes,
        "capture_quota_bytes": process.capture_quota_bytes,
        "quota_source": (
            "EXPLICIT_RESOURCE_PROFILE"
            if process.capture_quota_bytes is not None
            else "DISK_SPOOL_NO_GENERIC_PRODUCT_LIMIT"
        ),
    }


def _execution_codex_event_diagnostic(
    output: bytes, requested_model: str
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Reuse the frozen Execution JSONL parser without adopting its disabled route."""

    if _ExecutionCodexCliAdapter is None or _ExecutionCodexAdapterViolation is None:
        return None, "Execution_CODEX_PARSER_UNAVAILABLE"
    try:
        adapter = object.__new__(_ExecutionCodexCliAdapter)
        return (
            adapter.parse_event_stream(
                output,
                requested_model=requested_model,
                invocation=None,
            ),
            None,
        )
    except Exception as error:
        if isinstance(error, _ExecutionCodexAdapterViolation):
            return None, str(error).split(":", 1)[0]
        raise


def _codex_agent_message_from_events(output: bytes) -> str:
    """Extract the sole final agent message after the Execution parser validates JSONL."""

    messages: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("CODEX_JSONL_INVALID") from error
        if not isinstance(event, Mapping) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            messages.append(text)
    if len(messages) != 1:
        raise ValueError("CODEX_OCR_AGENT_MESSAGE_NOT_UNIQUE")
    return messages[0]


def _resolved_file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
