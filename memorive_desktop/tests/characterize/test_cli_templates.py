"""Custom command-line model tools: template checks, output parsing, sharing, and runs.

Pins the behaviour of memorive.models.cli.templates and template_execution. Since
11fdcf9 a verification is saved under stable_configuration_identity (no file hashes), so a
CLI that updates itself keeps its verification; a value saved in the old format still counts
while the program's files are unchanged (verification_current). The runs use
the test Python as the "CLI" with a small script, so nothing outside the test starts.
Volatile values (paths, hashes of local files, durations) are left out of the golden files.
"""
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from memorive.models.cli import template_execution as execution
from memorive.models.cli import templates as templates
from tests.characterize.golden import check, outcome

HERE = Path(__file__).parent

FAKE_CLI = r'''
import json, sys
args = sys.argv[1:]
mode = args[args.index("--mode") + 1]
model = args[args.index("--model") + 1] if "--model" in args else "default-model"
prompt = sys.stdin.read() if "--stdin" in args else args[-1]
if mode == "fail":
    sys.exit(3)
if mode == "text":
    print("echo:" + prompt[:24])
elif mode == "json":
    print(json.dumps({"model": model, "result": {"reply": "MEMORIVE_READY"}, "usage": {"in": 5, "out": 2}}))
elif mode == "other_model":
    print(json.dumps({"model": "someone-else", "result": {"reply": "x"}, "usage": {"in": 1, "out": 1}}))
elif mode == "schema_breaking":
    print(json.dumps({"model": model, "result": {"answer": 1}, "usage": {"in": 1, "out": 1}}))
elif mode == "jsonl":
    for row in ({"type": "delta", "text": "MEMO"}, {"type": "delta", "text": "RIVE"}, {"type": "done", "model": model}):
        print(json.dumps(row))
elif mode == "empty":
    pass
'''


def _template(**changes):
    value = templates.default_template()
    value.update(changes)
    return value


def _json_template(script, mode="json"):
    return _template(argv=[str(script), "--mode", mode, "--model", "{{model}}", "{{prompt}}"],
                     output={"mode": "json", "path": "result"},
                     capabilities={"structured_output": True, "images": False, "usage": True},
                     metadata={"model_path": "model", "usage_paths": {"prompt_tokens": "usage.in", "completion_tokens": "usage.out"}})


def _service(template, **changes):
    value = dict(config_id="custom-characterize", adapter_id=templates.ADAPTER, display_name="Fake CLI",
                 executable=sys.executable, enabled=True, connection_status="AVAILABLE", models=[],
                 template=template, interpreter="", environment={}, concurrency_group="")
    value.update(changes)
    return value


def _model(service, **changes):
    value = dict(profile_ref="cli:custom-characterize:1", display_name="Fake", model_name="fake-model", thinking_mode="")
    value["verification"] = {"configuration_sha256": templates.configuration_identity(service, value)}
    value.update(changes)
    return value


class _Process:
    def run_template(self, *, argv, cwd, environment, stdin_bytes, timeout_seconds, shell, capture_quota_bytes, result_paths):
        done = subprocess.run(argv, cwd=cwd, env=environment, input=stdin_bytes, capture_output=True,
                              timeout=timeout_seconds, shell=shell)
        return SimpleNamespace(started=True, timed_out=False, output_truncated=len(done.stdout) > capture_quota_bytes,
                               returncode=done.returncode, stdout=done.stdout), {}


class _Runner:
    """Just the parts of the settings model runner that execute() and verify() use."""

    def __init__(self, scratch):
        self.scratch_root = Path(scratch)
        self.process_transport = _Process()

    def preferences(self):
        return "system", "", None

    def safe_environment(self, kind, workspace, proxy_mode, proxy_address):
        keep = ("SystemRoot", "SYSTEMROOT", "PATH", "TEMP", "TMP", "COMSPEC")
        return {name: os.environ[name] for name in keep if name in os.environ}

    def _credential_resolver(self, ref):
        return b"secret-from-store"

    def _base(self, kind, *, status, reason):
        return dict(kind=kind, status=status, reason=reason)


RECEIPT_FIELDS = ("status", "failure_reason", "purpose", "profile_kind", "route", "requested_model", "returned_model",
                  "requested_model_mapping", "token_usage", "usage_status", "model_identity_evidence",
                  "request_model_binding_status", "process_started", "parser_version", "template_revision")


def _summary(result):
    receipt = result["execution_receipt"]
    return {"status": result["status"], "reason": result["reason"], "requested_model": result["requested_model"],
            "returned_model": result["returned_model"], "response": result.get("response"),
            "external_process_launches": result["external_process_launches"],
            "receipt": {k: receipt.get(k) for k in RECEIPT_FIELDS},
            "receipt_fields": sorted(receipt), "diagnostic_fields": sorted(receipt["transport_diagnostic"])}


@pytest.fixture
def fake_cli(tmp_path):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf8")
    return script


def test_template_validation():
    variants = {
        "default": {},
        "prompt_inside_other_text": {"argv": ["x{{prompt}}"]},
        "secret_argument": {"argv": ["--api-key=abc", "{{prompt}}"]},
        "unknown_placeholder": {"argv": ["{{unknown}}", "{{prompt}}"]},
        "unbalanced_braces": {"argv": ["{{prompt}}", "{{model"]},
        "file_input_without_slot": {"input": "file"},
        "file_input_with_slot": {"input": "file", "argv": ["--in", "{{prompt_file}}"]},
        "stdin_with_prompt_slot": {"input": "stdin"},
        "stdin_without_slot": {"input": "stdin", "argv": ["--quiet"]},
        "dynamic_interpreter_argument": {"interpreter_argv": ["{{model}}"]},
        "images_without_slot": {"capabilities": {"structured_output": False, "images": True, "usage": False}},
        "image_slot_without_capability": {"argv": ["{{prompt}}", "{{image_file}}"]},
        "usage_without_paths": {"capabilities": {"structured_output": False, "images": False, "usage": True}},
        "text_with_metadata": {"metadata": {"model_path": "model"}},
        "jsonl_without_events": {"output": {"mode": "jsonl"}},
        "jsonl_without_final_rule": {"output": {"mode": "jsonl", "events": [{"match": {"type": "delta"}, "path": "text", "operation": "append"}]}},
        "file_without_response_slot": {"output": {"mode": "file", "file_format": "text"}},
        "file_text_with_path": {"argv": ["{{prompt}}", "{{response_file}}"], "output": {"mode": "file", "file_format": "text", "path": "x"}},
        "text_with_unsupported_field": {"output": {"mode": "text", "path": "x"}},
        "timeout_zero": {"timeout_seconds": 0},
        "timeout_bool": {"timeout_seconds": True},
        "revision_zero": {"revision": 0},
        "other_schema": {"schema_version": "CLIConnectionTemplate-v2"},
        "extra_field": {"unexpected": 1},
        "working_directory_other": {"working_directory": "home"},
        "recursive_model_map": {"model_map": {"a": "{{model}}"}},
        "long_model_name": {"model_map": {"a": "x" * 129}},
        "bad_event_operation": {"output": {"mode": "jsonl", "events": [{"match": {"type": "d"}, "path": "t", "operation": "insert"}]},
                                "termination": {"success": [{"type": "done"}], "failure": []}},
        "bad_match_value": {"output": {"mode": "json"}, "termination": {"success": [{"type": [1]}], "failure": []}},
    }
    cases = {}
    for name, changes in variants.items():
        value = _template()
        value.update(changes)
        cases[name] = outcome(templates.validate_template, value)
    check("cli_template_validation", cases, HERE)


def test_output_parsing(tmp_path):
    jsonl = _template(argv=["{{prompt}}"], output={"mode": "jsonl", "dedupe_path": "id", "events": [
        {"match": {"type": "delta"}, "path": "text", "operation": "append"},
        {"match": {"type": "final"}, "path": "text", "operation": "replace"}]},
        termination={"success": [{"type": "done"}], "failure": [{"type": "error"}]},
        capabilities={"structured_output": False, "images": False, "usage": True},
        metadata={"model_path": "model", "usage_paths": {"total_tokens": "usage.total"}})
    json_template = _template(argv=["{{prompt}}"], output={"mode": "json", "path": "a.b"})
    file_text = _template(argv=["{{prompt}}", "{{response_file}}"], output={"mode": "file", "file_format": "text"})
    file_json = _template(argv=["{{prompt}}", "{{response_file}}"], output={"mode": "file", "file_format": "json", "path": "answer"})

    def lines(*rows):
        return ("\n".join(json.dumps(r) if not isinstance(r, str) else r for r in rows) + "\n").encode()

    result_text, result_json = tmp_path / "r.txt", tmp_path / "r.json"
    result_text.write_text("from the file", encoding="utf8")
    result_json.write_text(json.dumps({"answer": {"k": "v"}}), encoding="utf8")
    cases = {
        "text": outcome(templates.parse_output, _template(), b"plain answer\n"),
        "text_empty": outcome(templates.parse_output, _template(), b"  \n"),
        "text_not_utf8": outcome(templates.parse_output, _template(), b"\xff\xfe"),
        "json_path": outcome(templates.parse_output, json_template, b'{"a": {"b": "deep"}}'),
        "json_object_value": outcome(templates.parse_output, _template(argv=["{{prompt}}"], output={"mode": "json", "path": "a"}), b'{"a": {"b": 1}}'),
        "json_invalid": outcome(templates.parse_output, json_template, b"{nope"),
        "json_missing_path": outcome(templates.parse_output, json_template, b'{"a": {}}'),
        "jsonl_complete": outcome(templates.parse_output, jsonl, lines(
            {"id": 1, "type": "delta", "text": "Hel", "model": "m1"}, {"id": 1, "type": "delta", "text": "Hel", "model": "m1"},
            {"id": 2, "type": "delta", "text": "lo"}, {"id": 3, "type": "done", "usage": {"total": 9}})),
        "jsonl_final_replaces": outcome(templates.parse_output, jsonl, lines(
            {"type": "delta", "text": "draft"}, {"type": "final", "text": "Final"}, {"type": "done"})),
        "jsonl_delta_after_final": outcome(templates.parse_output, jsonl, lines(
            {"type": "final", "text": "Final"}, {"type": "delta", "text": "x"}, {"type": "done"})),
        "jsonl_no_terminal": outcome(templates.parse_output, jsonl, lines({"type": "delta", "text": "x"})),
        "jsonl_error_event": outcome(templates.parse_output, jsonl, lines({"type": "delta", "text": "x"}, {"type": "error"})),
        "jsonl_id_conflict": outcome(templates.parse_output, jsonl, lines(
            {"id": 1, "type": "delta", "text": "a"}, {"id": 1, "type": "delta", "text": "b"}, {"type": "done"})),
        "jsonl_model_conflict": outcome(templates.parse_output, jsonl, lines(
            {"type": "delta", "text": "a", "model": "m1"}, {"type": "delta", "text": "b", "model": "m2"}, {"type": "done"})),
        "jsonl_negative_usage": outcome(templates.parse_output, jsonl, lines({"type": "delta", "text": "a"}, {"type": "done", "usage": {"total": -1}})),
        "jsonl_not_json": outcome(templates.parse_output, jsonl, lines("progress...", {"type": "done"})),
        "file_text": outcome(templates.parse_output, file_text, b"", result_file=result_text),
        "file_json": outcome(templates.parse_output, file_json, b"", result_file=result_json),
        "file_missing": outcome(templates.parse_output, file_text, b"", result_file=tmp_path / "absent.txt"),
    }
    check("cli_template_parsing", cases, HERE)


def test_export_and_import():
    template = _template(argv=["--config=C:\\Users\\me\\cfg.toml", "--url=https://example.org/x", "file:///tmp/a",
                               "/home/me/run", "--model", "{{model}}", "{{prompt}}"])
    service = _service(template, models=[{"display_name": "Fast", "model_name": "m-fast", "thinking_mode": "low",
                                          "profile_ref": "cli:x:1", "connection_status": "AVAILABLE"}],
                       environment={"API_TOKEN": "ENV:MY_TOKEN"}, concurrency_group="g1")
    exported = templates.export_service(service)
    imported = templates.import_service(deepcopy(exported))
    imported_view = {k: v for k, v in imported.items() if k not in ("config_id",)}
    imported_view["models"] = [{k: v for k, v in row.items() if k != "profile_ref"} for row in imported["models"]]
    cases = {
        "exported": exported,
        "imported": imported_view,
        "import_other_schema": outcome(templates.import_service, dict(exported, schema_version="CLIConnectionExport-v2")),
        "import_extra_field": outcome(templates.import_service, dict(exported, extra=1)),
        "export_builtin_service": outcome(templates.export_service, dict(service, adapter_id="codex_cli")),
    }
    check("cli_template_sharing", cases, HERE)


def test_configuration_identity(fake_cli):
    template = _json_template(fake_cli)
    service = _service(template)
    model = _model(service)
    base = templates.configuration_identity(service, model)
    # Labels, switches and the list of models do not change the identity ...
    assert templates.configuration_identity(dict(service, display_name="Renamed", enabled=False,
                                                 connection_status="INVALID", models=[model]), model) == base
    assert templates.configuration_identity(service, dict(model, display_name="Other label")) == base
    # ... what the command runs does.
    assert templates.configuration_identity(service, dict(model, model_name="fake-model-2")) != base
    assert templates.configuration_identity(service, dict(model, thinking_mode="high")) != base
    assert templates.configuration_identity(_service(dict(template, timeout_seconds=60)), model) != base
    assert templates.configuration_identity(service, None) != base
    stable = templates.stable_configuration_identity(service, model)
    assert templates.stable_configuration_identity(dict(service, display_name="Renamed", enabled=False), model) == stable
    assert templates.stable_configuration_identity(service, dict(model, model_name="fake-model-2")) != stable
    assert templates.stable_configuration_identity(_service(dict(template, timeout_seconds=60)), model) != stable
    # configuration_identity also covers the bytes of the CLI program and its launch files, so an
    # update of the tool changes it; it stays as the key of the exam score cache. The identity a
    # verification is saved under (stable_configuration_identity) does not change.
    fake_cli.write_text(FAKE_CLI + "\n# updated\n", encoding="utf8")
    assert templates.configuration_identity(service, model) != base
    assert templates.stable_configuration_identity(service, model) == stable


def test_verification_current(fake_cli):
    service = _service(_json_template(fake_cli))
    model = {"profile_ref": "cli:custom-characterize:1", "model_name": "fake-model", "thinking_mode": ""}
    new_value = {"configuration_sha256": templates.stable_configuration_identity(service, model)}
    old_value = {"configuration_sha256": templates.configuration_identity(service, model)}
    cases = {
        "new_format": templates.verification_current(new_value, service, model),
        "old_format_tool_unchanged": templates.verification_current(old_value, service, model),
        "nothing_saved": templates.verification_current({}, service, model),
        "no_binding": templates.verification_current(None, service, model),
        "other_model": templates.verification_current(new_value, service, dict(model, model_name="fake-model-2")),
    }
    fake_cli.write_text(FAKE_CLI + "\n# updated\n", encoding="utf8")
    cases["new_format_tool_updated"] = templates.verification_current(new_value, service, model)
    cases["old_format_tool_updated"] = templates.verification_current(old_value, service, model)
    check("cli_template_verification_current", cases, HERE)


def test_runs(fake_cli, tmp_path):
    runner = _Runner(tmp_path / "scratch")
    json_service = _service(_json_template(fake_cli))
    model = _model(json_service)
    schema = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"], "additionalProperties": False}
    text_service = _service(_template(argv=[str(fake_cli), "--mode", "text", "--model", "{{model}}", "{{prompt}}"]))
    text_model = _model(text_service)
    stdin_service = _service(_template(input="stdin", argv=[str(fake_cli), "--mode", "text", "--stdin"]))
    jsonl_service = _service(_template(
        argv=[str(fake_cli), "--mode", "jsonl", "--model", "{{model}}", "{{prompt}}"],
        output={"mode": "jsonl", "events": [{"match": {"type": "delta"}, "path": "text", "operation": "append"}]},
        termination={"success": [{"type": "done"}], "failure": []}, metadata={"model_path": "model"}))

    def run(service, model_value, mode=None, **kwargs):
        if mode:
            service = deepcopy(service)
            argv = service["template"]["argv"]
            argv[argv.index("--mode") + 1] = mode
            model_value = _model(service, **{k: v for k, v in model_value.items() if k == "model_name"})
        return _summary(execution.execute(runner, service, model_value, "Reply please", **kwargs))

    cases = {
        "verify_text": {k: v for k, v in execution.verify(runner, text_service, text_model).items()
                        if k not in ("duration_ms", "execution_receipt", "cli_diagnostic", "resolved_executable")},
        "verify_structured": {k: v for k, v in execution.verify(runner, json_service, model).items()
                              if k not in ("duration_ms", "execution_receipt", "cli_diagnostic", "resolved_executable")},
        "chat_text": run(text_service, text_model),
        "chat_stdin": run(stdin_service, _model(stdin_service)),
        "chat_structured": run(json_service, model, purpose="structured_chat", schema=schema),
        "chat_jsonl": run(jsonl_service, _model(jsonl_service)),
        "unverified_model": run(json_service, dict(model, verification={}), purpose="structured_chat"),
        "no_model_name": run(json_service, dict(model, model_name=""), purpose="cli_verification"),
        "input_too_large": run(_service(_json_template(fake_cli) | {"max_input_bytes": 4}), model),
        "exit_nonzero": run(json_service, model, mode="fail"),
        "empty_output": run(text_service, text_model, mode="empty"),
        "other_model_returned": run(json_service, model, mode="other_model"),
        "schema_not_met": run(json_service, model, mode="schema_breaking", purpose="structured_chat", schema=schema),
        "images_unsupported": run(json_service, model, image=b"png"),
        "env_unbound": run(_service(_json_template(fake_cli), environment={"TOKEN": "ENV:MEMORIVE_TEST_SURELY_UNSET"}), model),
        "executable_missing": run(_service(_json_template(fake_cli), executable="memorive-no-such-cli.exe"), model),
    }
    # Verified, then the CLI updates itself. A verification saved in the old format (the hash of the
    # program's files) cannot be checked any more and is refused once; one saved in the current
    # format (stable_configuration_identity) stays valid.
    verified = _model(json_service)
    stable_verified = dict(verified, verification={
        "configuration_sha256": templates.stable_configuration_identity(json_service, verified)})
    fake_cli.write_text(FAKE_CLI + "\n# updated\n", encoding="utf8")
    cases["after_cli_update_chat"] = run(json_service, verified, purpose="structured_chat", schema=schema)
    cases["after_cli_update_chat_current_format"] = run(json_service, stable_verified, purpose="structured_chat", schema=schema)
    cases["after_cli_update_verify"] = run(json_service, verified, purpose="cli_verification")
    check("cli_template_runs", cases, HERE)
