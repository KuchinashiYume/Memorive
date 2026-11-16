"""When a failed model call is tried again, and how long to wait before the next try.

Every model call made for a feature follows these rules: the workflow's nodes
(memorive/documents/workflow/model_bridge.py) and the research Q&A (runs.py). Change retries here.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from memorive.models.calls.ledger import execution_checkpoint

# How often a research question is sent again after a temporary failure (so at most three sends).
RESEARCH_CHAT_RETRIES = 2
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
# Connection errors after which the provider may or may not have finished the request.
BROKEN_STREAM_ERRORS = {'IncompleteRead', 'ConnectionResetError', 'RemoteDisconnected', 'ConnectionAbortedError',
                        'BrokenPipeError'}
UNKNOWN_STATUS_ERRORS = {'RemoteDisconnected', 'ConnectionResetError', 'ConnectionAbortedError', 'BrokenPipeError',
                         'TimeoutError'}
TEMPORARY_REASON_MARKERS = ('TIMEOUT', 'TEMPORAR', 'CONNECTION', 'NETWORK', 'RATE_LIMIT', 'HTTP_429', 'HTTP_500',
                            'HTTP_502', 'HTTP_503', 'HTTP_504', 'HTTP_400_UNCLASSIFIED_RETRYABLE')
LASTING_REASON_MARKERS = ('JSON_TRUNCATED', 'RESPONSE_EMPTY', 'SCHEMA', 'PARSE')


def is_temporary_failure(result: Mapping[str, Any]) -> bool:
    """Whether a failed call may succeed when it is sent again."""
    if result.get("status") == "PASS":
        return False
    receipt = result.get("execution_receipt")
    diagnostic = receipt.get("transport_diagnostic") if isinstance(receipt, Mapping) else None
    # The answer was cut at the output limit: sending it again gives the same cut.
    if isinstance(diagnostic, Mapping) and str(diagnostic.get("finish_reason") or "").lower() == "length":
        return False
    reason = str(result.get("reason") or "").upper()
    if reason == 'API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN':
        # The stream broke after the provider started answering. Sending again may be charged a
        # second time but repeats no tool action; partial output is never taken as a success.
        return (result.get('status') == 'FAILED' and isinstance(diagnostic, Mapping)
                and diagnostic.get('stream') is True and diagnostic.get('complete') is False
                and diagnostic.get('exception_type') in BROKEN_STREAM_ERRORS)
    classification = str(diagnostic.get("classification") or "").upper() if isinstance(diagnostic, Mapping) else ""
    http_status = diagnostic.get("http_status") if isinstance(diagnostic, Mapping) else None
    if isinstance(http_status, str) and http_status.isascii() and http_status.isdecimal():
        http_status = int(http_status)
    if isinstance(http_status, int) and not isinstance(http_status, bool) and 400 <= http_status <= 599:
        if http_status == 400 and classification == "UNCLASSIFIED_RETRYABLE":
            return True
        # The HTTP status reported by the transport decides over the wording of the reason.
        return http_status in RETRYABLE_HTTP_STATUS
    if classification == "UNCLASSIFIED_RETRYABLE":
        return True
    if result.get("status") == "FAILED" and reason in {"STRUCTURED_CHAT_RESPONSE_INVALID", "STRUCTURED_CHAT_RESPONSE_EMPTY"}:
        # A finished answer can still hold malformed JSON; nothing was accepted from it, so generate again.
        return True
    if result.get("status") == "FAILED" and reason == "API_RESPONSE_STATUS_UNKNOWN":
        return isinstance(diagnostic, Mapping) and diagnostic.get("exception_type") in UNKNOWN_STATUS_ERRORS
    if any(marker in reason for marker in LASTING_REASON_MARKERS):
        return False
    return any(marker in reason for marker in TEMPORARY_REASON_MARKERS)


def research_chat_should_retry(result: Mapping[str, Any]) -> bool:
    """A research question is sent again after a temporary failure, and after an empty answer
    from a provider that reported a normal finish."""
    if result.get('status') in {'PASS', 'NOT_RUN', 'CANCELLED'}:
        return False
    diagnostic = (result.get('execution_receipt') or {}).get('transport_diagnostic') or {}
    if diagnostic.get('http_status') in {200, '200'} and diagnostic.get('finish_reason') == 'stop':
        if 'STRUCTURED_CHAT_RESPONSE_EMPTY' in str(result.get('reason', '')):
            return True
    return is_temporary_failure(result)


def wait_before_retry(retry_index: int) -> None:
    """Wait 1, 2, 4 ... seconds (at most 30) before try number retry_index + 2, stopping at once
    when the task is paused, cancelled or the app shuts down."""
    deadline = time.monotonic() + min(2 ** retry_index, 30)
    while True:
        execution_checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.1))
