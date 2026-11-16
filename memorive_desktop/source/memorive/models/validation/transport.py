"""How a request goes out and its reply comes back.

HttpResponse and ProcessResult are what a transport returns; HttpTransport and ProcessTransport are
the interfaces the runner calls, and tests pass fakes in their place. UrllibHttpTransport sends over
urllib without following redirects, reads streamed chat replies and stops when the call is cancelled;
BoundedSubprocessTransport runs a command-line tool without a shell and spools its output to disk.
MAX_PROCESS_CAPTURE_BYTES and MAX_EMBEDDING_HTTP_BODY_BYTES are old names kept for compatibility;
both are None, no size cap.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Mapping, Protocol, Sequence
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_EMBEDDING_HTTP_BODY_BYTES = None


MAX_PROCESS_CAPTURE_BYTES = None  # compatibility name; no implicit output quota


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    transport_diagnostic: Mapping[str, Any] | None = None


class _ResponseInterrupted(OSError):
    def __init__(self, partial: bytes, diagnostic: Mapping[str, Any]):
        super().__init__('API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN')
        self.partial = partial
        self.diagnostic = dict(diagnostic)


def _read_chat_stream(response, request_body: bytes | None) -> HttpResponse:
    """Reuse MODEL_GATEWAY SSE parsers. Heartbeats show connectivity, not model progress."""
    from model_gateway.providers.deepseek import _read_sse_response as read_openai
    from model_gateway.providers.anthropic import _read_sse_response as read_anthropic

    diagnostic = {'stream': True, 'received_bytes': 0, 'heartbeat_count': 0,
                  'progress_event_count': 0, 'last_byte_monotonic': None,
                  'last_progress_monotonic': None, 'complete': False,
                  'silence_is_disconnect': False, 'remote_cancellation_confirmed': False}
    request = json.loads(request_body or b'{}')
    # Anthropic uses messages without response_format; Gemini remains non-SSE.
    anthropic = 'response_format' not in request
    text_parts = []

    def observed_lines():
        for raw in response:
            diagnostic['received_bytes'] += len(raw)
            diagnostic['last_byte_monotonic'] = time.monotonic()
            line = raw.decode('utf-8').strip()
            if line.startswith(':'):
                diagnostic['heartbeat_count'] += 1
            elif line.startswith('data:') and line[5:].strip() not in ('', '[DONE]'):
                event = json.loads(line[5:].strip())
                if isinstance(event, Mapping):
                    if event.get('type') == 'ping':
                        diagnostic['heartbeat_count'] += 1
                    delta = event.get('delta') or {}
                    choices = event.get('choices') or []
                    if choices:
                        delta = choices[0].get('delta') or {}
                    if delta.get('content') or delta.get('text') or delta.get('reasoning_content') or delta.get('thinking'):
                        diagnostic['progress_event_count'] += 1
                        diagnostic['last_progress_monotonic'] = time.monotonic()
                    text = delta.get('content') or delta.get('text')
                    if isinstance(text, str):
                        text_parts.append(text)
            yield raw
    try:
        value, parsed = (read_anthropic if anthropic else read_openai)(observed_lines())
    except (IncompleteRead, OSError, ValueError, RuntimeError) as error:
        diagnostic['exception_type'] = type(error).__name__
        # Keep only visible answer text, not provider reasoning or credentials.
        partial = ''.join(text_parts).encode('utf-8')
        raise _ResponseInterrupted(partial, diagnostic) from error
    return HttpResponse(int(response.status), json.dumps(value, ensure_ascii=False).encode('utf-8'),
                        {**diagnostic, **parsed, 'complete': True})


@dataclass(frozen=True)
class ProcessResult:
    started: bool
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_truncated: bool = False
    duration_ms: int = 0
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    capture_quota_bytes: int | None = None


class HttpTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        proxy_mode: str,
        proxy_address: str,
        max_response_bytes: int | None = None,
    ) -> HttpResponse: ...


class ProcessTransport(Protocol):
    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: str,
        environment: Mapping[str, str],
        stdin_bytes: bytes | None,
        timeout_seconds: int,
        shell: bool,
    ) -> ProcessResult: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


class UrllibHttpTransport:
    """HTTPS client that never follows redirects with an API key.

    Model/provider capacity is enforced at the execution-profile layer.  This
    transport does not add a smaller generic one-megabyte response ceiling.
    """

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        proxy_mode: str,
        proxy_address: str,
        max_response_bytes: int | None = None,
    ) -> HttpResponse:
        if max_response_bytes is not None and (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError("API_RESPONSE_LIMIT_INVALID")
        if not url.startswith("https://"):
            raise ValueError("API_VALIDATION_HTTPS_REQUIRED")
        from memorive.models.calls.network import resolve_proxy, tls_context
        from urllib.request import HTTPSHandler
        proxy = resolve_proxy(url, proxy_mode, proxy_address)
        proxy_handler = ProxyHandler({'https': proxy} if proxy else {})
        opener = build_opener(proxy_handler, _NoRedirect(), HTTPSHandler(context=tls_context()))
        request = Request(url, data=body, headers=dict(headers), method=method)
        def read(response):
            response_headers = getattr(response, 'headers', {})
            if 'text/event-stream' in response_headers.get('Content-Type', ''):
                return _read_chat_stream(response, body)
            try:
                payload = response.read()
            except IncompleteRead as error:
                raise _ResponseInterrupted(error.partial, {
                    'stream': False, 'complete': False, 'exception_type': 'IncompleteRead',
                    'remote_cancellation_confirmed': False,
                }) from error
            return HttpResponse(status_code=int(response.status), body=payload)
        from memorive.models.calls.cancellable_http import read_response
        from memorive.models.calls.ledger import execution_interrupt_callback
        return read_response(opener.open, request, timeout=timeout_seconds,
            interrupt=execution_interrupt_callback(), reader=read, read_error_response=True)


class BoundedSubprocessTransport:
    """No-shell process runner with disk spooling and optional explicit quota."""

    def __init__(self, *, capture_quota_bytes: int | None = None):
        if capture_quota_bytes is None:
            raw = os.environ.get("MEMORIVE_CLI_CAPTURE_QUOTA_BYTES")
            capture_quota_bytes = int(raw) if raw and raw.strip() else None
        if capture_quota_bytes is not None and (
            isinstance(capture_quota_bytes, bool)
            or not isinstance(capture_quota_bytes, int)
            or capture_quota_bytes <= 0
        ):
            raise ValueError("CLI_CAPTURE_QUOTA_INVALID")
        self.capture_quota_bytes = capture_quota_bytes

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
            return
        process.kill()

    def run_verification(self, **kwargs):
        from memorive.models.cli.verification_transport import run_verification
        return run_verification(self, **kwargs)

    def run_template(self, *, capture_quota_bytes, **kwargs):
        from memorive.models.cli.verification_transport import run_verification
        transport=BoundedSubprocessTransport(capture_quota_bytes=capture_quota_bytes)
        return run_verification(transport, template_mode=True, **kwargs)

    def _read_bounded(self, path: Path) -> tuple[bytes, bool, int]:
        total = path.stat().st_size
        with path.open("rb") as stream:
            if self.capture_quota_bytes is None:
                value = stream.read()
                return value, False, total
            value = stream.read(self.capture_quota_bytes + 1)
        return (
            value[: self.capture_quota_bytes],
            total > self.capture_quota_bytes,
            total,
        )

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: str,
        environment: Mapping[str, str],
        stdin_bytes: bytes | None,
        timeout_seconds: int,
        shell: bool,
    ) -> ProcessResult:
        if shell is not False:
            raise ValueError("CLI_VALIDATION_SHELL_FORBIDDEN")
        workspace = Path(cwd)
        capture_workspace = workspace
        if (workspace / ".exclusive-call.lock").is_file():
            # Retain each attempt without colliding with a prior call in the
            # stable lane; only the child process cwd needs to stay constant.
            capture_workspace = workspace / "captures" / uuid.uuid4().hex
            capture_workspace.mkdir(parents=True, exist_ok=False)
        stdout_path = capture_workspace / "stdout.bin"
        stderr_path = capture_workspace / "stderr.bin"
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        started_at = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
                from memorive.models.cli.windows import native_process_path
                process = subprocess.Popen(
                    list(argv),
                    cwd=native_process_path(cwd),
                    env=dict(environment),
                    stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
                try:
                    process.communicate(input=stdin_bytes, timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate(process)
                    process.communicate(timeout=5)
            stdout, stdout_truncated, stdout_total = self._read_bounded(stdout_path)
            stderr, stderr_truncated, stderr_total = self._read_bounded(stderr_path)
            return ProcessResult(
                started=True,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                output_truncated=stdout_truncated or stderr_truncated,
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
                stdout_total_bytes=stdout_total,
                stderr_total_bytes=stderr_total,
                capture_quota_bytes=self.capture_quota_bytes,
            )
        except (FileNotFoundError, PermissionError, OSError):
            return ProcessResult(
                started=False,
                returncode=None,
                stdout=b"",
                stderr=b"",
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
            )
