"""Records that cross a boundary: cancel frames from the desktop window, commands from the test
console, effects queued for sending, model usage rows, research interaction events and the
folder a paper is stored in.
"""
from __future__ import annotations

import pytest

from memorive.platform.naming import TITLE_MAX
from memorive_accounting.projection import _normalize as accounting_row
from memorive_app.durable_store import DurableJobStore, _require_secret_free
from memorive_desktop_service.errors import ProtocolViolation, SchemaIncompatible
from memorive_desktop_service.protocol import CANCEL_SCHEMA, PROTOCOL_VERSION, validate_cancel
from memorive.platform.folders.policy import document_folder
from memorive_test_console_bridge.expressions import BY_ID
from memorive_test_console_bridge.protocol import validate_command
from research_recommendations.exposure_feedback import ExposureFeedbackError, validate_research_interaction_event


# Cancel frames on the desktop connection

def _cancel(**changes):
    value = {"schema_version": CANCEL_SCHEMA, "protocol_version": PROTOCOL_VERSION, "request_id": "req-2",
             "target_request_id": "req-1"}
    value.update(changes)
    return value


def test_a_cancel_frame_names_the_request_it_stops():
    assert validate_cancel(_cancel())["target_request_id"] == "req-1"


@pytest.mark.parametrize("value, error, code", [
    ({k: v for k, v in _cancel().items() if k != "target_request_id"}, ProtocolViolation,
     "IPC_CANCEL_MISSING_FIELD:target_request_id"),
    (_cancel(reason="user"), ProtocolViolation, "IPC_CANCEL_UNKNOWN_FIELD:reason"),
    (_cancel(schema_version="DesktopIPCRequest-v1"), SchemaIncompatible, "IPC_CANCEL_SCHEMA_UNSUPPORTED"),
    (_cancel(protocol_version="2.0"), SchemaIncompatible, "IPC_PROTOCOL_VERSION_UNSUPPORTED:2.0"),
    (_cancel(request_id=""), ProtocolViolation, "IPC_REQUEST_ID_INVALID"),
    (_cancel(target_request_id="../req"), ProtocolViolation, "IPC_TARGET_REQUEST_ID_INVALID"),
    (_cancel(target_request_id="r" * 129), ProtocolViolation, "IPC_TARGET_REQUEST_ID_INVALID"),
    (_cancel(target_request_id=7), ProtocolViolation, "IPC_TARGET_REQUEST_ID_INVALID"),
])
def test_a_malformed_cancel_frame_is_refused(value, error, code):
    with pytest.raises(error) as caught:
        validate_cancel(value)
    assert str(caught.value) == code


# Test console commands

def _command(operation, params, allow=False, request_id="cmd-1"):
    return {"request_id": request_id, "operation": operation, "params": params, "allow_model_calls": allow}


@pytest.mark.parametrize("value", [
    _command("message.test", {"kind": "info", "title": "Hello", "body": "World"}),
    _command("report.run", {"period": "weekly"}),
    _command("discovery.recommend", {"topic": "enzymes", "count": 20}, allow=True),
    _command("inbox.import", {"paths": [r"C:\data\paper.pdf"]}),
    _command("task.cancel", {"job_id": "job:1"}),
    _command("diagnostics", {}),
    _command("expression.trigger", {"event_id": next(iter(BY_ID)), "duration_ms": 500}),
])
def test_well_formed_console_commands_are_accepted(value):
    assert validate_command(value) is value


@pytest.mark.parametrize("value, code", [
    ([], "COMMAND_FIELDS_INVALID"),
    (dict(_command("diagnostics", {}), extra=1), "COMMAND_FIELDS_INVALID"),
    (_command("diagnostics", {}, request_id="-bad"), "IDENTIFIER_INVALID"),
    (_command("diagnostics", {}, allow="no"), "MODEL_PERMISSION_BOOLEAN_REQUIRED"),
    (_command("shell.run", {}), "OPERATION_UNKNOWN"),
    (_command("report.run", {}), "PARAMETER_FIELDS_INVALID"),
    (_command("report.run", {"period": "daily", "extra": "x"}), "PARAMETER_FIELDS_INVALID"),
    (_command("report.run", "daily"), "PARAMETER_FIELDS_INVALID"),
    (_command("task.retry", {"job_id": "job-1"}), "MANUAL_MODEL_PERMISSION_REQUIRED"),
    (_command("message.test", {"kind": "info", "title": "t", "body": "b" * (130 * 1024)}), "PARAMETERS_TOO_LARGE"),
    (_command("message.test", {"kind": "info", "title": "t", "body": "key sk-" + "a" * 24}), "CREDENTIAL_VALUE_NOT_ALLOWED"),
    (_command("message.test", {"kind": "info", "title": "t", "body": "use ghp_" + "a" * 24}), "CREDENTIAL_VALUE_NOT_ALLOWED"),
    (_command("message.test", {"kind": "info", "title": "t", "body": "Bearer abc.def"}), "CREDENTIAL_VALUE_NOT_ALLOWED"),
    (_command("expression.trigger", {"event_id": next(iter(BY_ID)), "duration_ms": 500}, allow=True),
     "EXPRESSION_MODEL_PERMISSION_FORBIDDEN"),
    (_command("expression.trigger", {"event_id": "unknown", "duration_ms": 500}), "EXPRESSION_EVENT_UNKNOWN"),
    (_command("expression.trigger", {"event_id": next(iter(BY_ID)), "duration_ms": 499}), "EXPRESSION_DURATION_INVALID"),
    (_command("expression.trigger", {"event_id": next(iter(BY_ID)), "duration_ms": 5001}), "EXPRESSION_DURATION_INVALID"),
    (_command("expression.trigger", {"event_id": next(iter(BY_ID)), "duration_ms": 1000.0}), "EXPRESSION_DURATION_INVALID"),
    (_command("task.cancel", {"job_id": "job 1"}), "IDENTIFIER_INVALID"),
    (_command("inbox.import", {"paths": ["paper.pdf"]}), "ABSOLUTE_IMPORT_PATHS_REQUIRED"),
    (_command("inbox.import", {"paths": []}), "ABSOLUTE_IMPORT_PATHS_REQUIRED"),
    (_command("inbox.import", {"paths": [r"C:\p.pdf"] * 33}), "ABSOLUTE_IMPORT_PATHS_REQUIRED"),
    (_command("discovery.recommend", {"topic": "t", "count": 21}, allow=True), "COUNT_INVALID"),
    (_command("discovery.recommend", {"topic": "t", "count": True}, allow=True), "COUNT_INVALID"),
    (_command("discovery.recommend", {"topic": " ", "count": 5}, allow=True), "DISCOVERY_TOPIC_REQUIRED"),
    (_command("event.inject", {"event": "explode", "note": ""}), "EVENT_INVALID"),
    (_command("event.inject", {"event": "fail", "note": "n" * 3501}), "TEXT_PARAMETER_INVALID"),
    (_command("message.test", {"kind": "alarm", "title": "t", "body": "b"}), "MESSAGE_INVALID"),
    (_command("message.test", {"kind": "info", "title": " ", "body": "b"}), "MESSAGE_INVALID"),
    (_command("message.test", {"kind": "info", "title": "t", "body": " "}), "MESSAGE_INVALID"),
    (_command("message.test", {"kind": "info", "title": "t" * 181, "body": "b"}), "MESSAGE_TITLE_TOO_LONG"),
    (_command("report.run", {"period": "yearly"}), "REPORT_PERIOD_INVALID"),
])
def test_console_commands_outside_the_contract_are_refused(value, code):
    with pytest.raises(ValueError) as caught:
        validate_command(value)
    assert str(caught.value) == code


# Effects queued for sending

@pytest.mark.parametrize("payload, where", [
    ({"api_key": "x"}, "payload.api_key"),
    ({"request": {"headers": {"Authorization_Token": "x"}}}, "payload.request.headers.Authorization_Token"),
    ({"items": [{"ok": 1}, {"Password": "x"}]}, "payload.items[1].Password"),
    ({"apiKey": "x"}, "payload.apiKey"),
    ({"client_secret": "x"}, "payload.client_secret"),
    ({"credential-value": "x"}, "payload.credential-value"),
])
def test_an_effect_carrying_a_secret_field_is_not_queued(tmp_path, payload, where):
    store = DurableJobStore(tmp_path / "jobs.sqlite3", owner_id="test")
    with pytest.raises(ValueError) as caught:
        store.enqueue_external_effect("job-1", effect_type="notify", payload=payload, idempotency_key="k1")
    assert str(caught.value) == "SENSITIVE_OUTBOX_FIELD_FORBIDDEN:" + where


@pytest.mark.parametrize("payload", [
    {"credential_env": "OPENALEX_API_KEY", "tokens_input": 3, "max_tokens": 10, "title": "secret garden"},
    ["token", {"note": "password reset requested"}],
])
def test_field_names_that_only_mention_secrets_pass(payload):
    assert _require_secret_free(payload) is None


def test_a_receipt_carrying_a_secret_field_is_refused():
    with pytest.raises(ValueError, match="SENSITIVE_OUTBOX_FIELD_FORBIDDEN:receipt.response.token"):
        _require_secret_free({"response": {"token": "x"}}, "receipt")


# Model usage rows

def _usage(**changes):
    value = {"attempt_id": "a1", "occurred_at": "2026-11-15T01:00:00Z", "job_id": "job-1", "profile_ref": "p1",
             "profile_kind": "API", "provider_label": "Provider", "model_label": "Model", "model_id": "model",
             "outcome": "SUCCEEDED", "tokens_input": 10, "tokens_output": 5, "tokens_reasoning": 2,
             "cost_status": "ACTUAL", "cost_value": "0.01", "currency": "usd", "source_kind": "ACCOUNTING_ENVELOPE"}
    value.update(changes)
    return value


def test_a_usage_row_is_normalized():
    row = accounting_row(_usage())
    assert (row["currency"], str(row["cost_value"]), row["occurred_at"].isoformat()) == (
        "USD", "0.01", "2026-11-15T01:00:00+00:00")
    unassessed = accounting_row(_usage(cost_status="NOT_ASSESSED", cost_value="unknown", currency=None))
    assert (str(unassessed["cost_value"]), unassessed["currency"]) == ("0", None)


@pytest.mark.parametrize("changes, code", [
    ({"extra": 1}, "ACCOUNTING_ATTEMPT_KEY_SET_INVALID"),
    ({"tokens_output": -1}, "ACCOUNTING_OUTPUT_TOKENS_INVALID"),
    ({"tokens_output": True}, "ACCOUNTING_OUTPUT_TOKENS_INVALID"),
    ({"tokens_output": "five"}, "ACCOUNTING_OUTPUT_TOKENS_INVALID"),
    ({"tokens_reasoning": None}, "ACCOUNTING_REASONING_TOKENS_INVALID"),
    ({"tokens_input": -3}, "ACCOUNTING_INPUT_TOKENS_INVALID"),
    ({"tokens_reasoning": 6}, "ACCOUNTING_REASONING_NOT_SUBSET_OF_OUTPUT"),
    ({"profile_kind": "BROWSER"}, "ACCOUNTING_PROFILE_KIND_INVALID"),
    ({"outcome": "PARTIAL"}, "ACCOUNTING_OUTCOME_INVALID"),
    ({"cost_status": "GUESSED"}, "ACCOUNTING_COST_STATUS_INVALID"),
    ({"source_kind": "SCREEN_SCRAPE"}, "ACCOUNTING_SOURCE_KIND_INVALID"),
    ({"currency": "US"}, "ACCOUNTING_CURRENCY_INVALID"),
    ({"currency": "U$D"}, "ACCOUNTING_CURRENCY_INVALID"),
    ({"currency": ""}, "ACCOUNTING_ASSESSABLE_CURRENCY_MISSING"),
    ({"cost_status": "ESTIMATED", "currency": None}, "ACCOUNTING_ASSESSABLE_CURRENCY_MISSING"),
    ({"job_id": ""}, "ACCOUNTING_JOB_ID_INVALID"),
    ({"job_id": "j" * 193}, "ACCOUNTING_JOB_ID_INVALID"),
    ({"job_id": 7}, "ACCOUNTING_JOB_ID_INVALID"),
    ({"occurred_at": "2026-11-15T01:00:00"}, "ACCOUNTING_TIMESTAMP_NAIVE"),
    ({"occurred_at": "yesterday"}, "ACCOUNTING_TIMESTAMP_INVALID"),
    ({"cost_value": "-0.01"}, "ACCOUNTING_COST_INVALID"),
    ({"cost_value": "NaN"}, "ACCOUNTING_COST_INVALID"),
    ({"cost_value": "about a cent"}, "ACCOUNTING_COST_INVALID"),
])
def test_a_usage_row_that_does_not_add_up_is_refused(changes, code):
    with pytest.raises(ValueError) as caught:
        accounting_row(_usage(**changes))
    assert str(caught.value) == code


def test_a_usage_row_without_a_job_is_kept():
    assert accounting_row(_usage(job_id=None))["job_id"] is None


# Research interaction events

def _event(**changes):
    value = {"schema_version": "discovery.verification.research-interaction-evidence.0.1",
             "object_type": "ResearchInteractionEvidence", "query_hash": "A" * 64, "event_time": "2026-11-15T01:00:00Z",
             "session_hash": "B" * 64, "field_dimension": "biology", "cited_work_id": "W1",
             "completion_state": "COMPLETED", "origin": "USER_INITIATED", "exposure_id": None}
    value.update(changes)
    return value


@pytest.mark.parametrize("value", [
    _event(),
    _event(cited_work_id=None, completion_state="CANCELLED"),
    _event(origin="Discovery_EXPOSURE_FOLLOWUP", exposure_id="EXPOSURE:1"),
])
def test_a_research_interaction_event_with_hashes_only_is_accepted(value):
    assert validate_research_interaction_event(value) is None


@pytest.mark.parametrize("value, code", [
    (dict(_event(), raw_query="what is x"), "FORBIDDEN_PRIVATE_OR_RAW_FIELD"),
    (dict(_event(), extra=1), "Research_INTERACTION_FIELD_SET_INVALID"),
    (_event(schema_version="0.0"), "Research_INTERACTION_SCHEMA_INVALID"),
    (_event(object_type="Other"), "Research_INTERACTION_SCHEMA_INVALID"),
    (_event(query_hash="a" * 64), "QUERY_HASH_INVALID"),
    (_event(query_hash="what is x"), "QUERY_HASH_INVALID"),
    (_event(session_hash="B" * 63), "SESSION_HASH_INVALID"),
    (_event(event_time="2026-11-15T01:00:00"), "TIMESTAMP_INVALID"),
    (_event(event_time="later"), "TIMESTAMP_INVALID"),
    (_event(origin="SCRAPED"), "Research_ORIGIN_INVALID"),
    (_event(field_dimension=""), "FIELD_DIMENSION_INVALID"),
    (_event(completion_state="UNKNOWN"), "Research_INTERACTION_SCHEMA_INVALID"),
    (_event(cited_work_id=""), "Research_INTERACTION_SCHEMA_INVALID"),
    (_event(origin="Discovery_EXPOSURE_FOLLOWUP"), "Discovery_FOLLOWUP_EXPOSURE_REQUIRED"),
    (_event(exposure_id="E1"), "Research_EXPOSURE_ID_INVALID"),
])
def test_a_research_interaction_event_outside_the_form_is_refused(value, code):
    with pytest.raises(ExposureFeedbackError) as caught:
        validate_research_interaction_event(value)
    assert caught.value.code == code


# The folder a paper is stored in

def test_a_paper_folder_is_named_from_its_id_and_cleaned_title(tmp_path):
    folder = document_folder(tmp_path, paper_id="Doe2024a", title='Rates: "fast" or slow?')
    assert folder == tmp_path.resolve() / "[Doe2024a] Rates - fast or slow"
    long = document_folder(tmp_path, paper_id="Doe2024a", title="word " * 40)
    assert len(long.name) <= len("[Doe2024a] ") + TITLE_MAX + 1


@pytest.mark.parametrize("paper_id, title, code", [
    ("", "Title", "FOLDER_POLICY_PAPER_ID_INVALID"),
    ("../Doe", "Title", "FOLDER_POLICY_PAPER_ID_INVALID"),
    ("Doe 2024", "Title", "FOLDER_POLICY_PAPER_ID_INVALID"),
    ("D" * 129, "Title", "FOLDER_POLICY_PAPER_ID_INVALID"),
    (2024, "Title", "FOLDER_POLICY_PAPER_ID_INVALID"),
    ("Doe2024", " ", "FOLDER_POLICY_DOCUMENT_TITLE_INVALID"),
    ("Doe2024", None, "FOLDER_POLICY_DOCUMENT_TITLE_INVALID"),
    ("Doe2024", '*?"<>|', "FOLDER_POLICY_DOCUMENT_TITLE_EMPTY_AFTER_SANITIZE"),
])
def test_a_paper_folder_needs_a_safe_id_and_a_usable_title(tmp_path, paper_id, title, code):
    with pytest.raises(ValueError) as caught:
        document_folder(tmp_path, paper_id=paper_id, title=title)
    assert str(caught.value) == code
