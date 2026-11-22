"""A model on a local OpenAI-compatible server (LM Studio, vLLM, LocalAI, Jan, llama.cpp) runs a structured chat.

Such models were listed and could be chosen for a workflow step, but every request failed with
LOCAL_STRUCTURED_CHAT_PROFILE_INVALID: only Ollama's native API could run. The server here is a stand-in
on this computer that answers /v1/chat/completions as those servers do.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from memorive.models.validation.runner import LiveModelValidationRunner

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


class _Server(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Server.seen.append((self.path, request))
        body = json.dumps({"model": request["model"], "usage": {"prompt_tokens": 12, "completion_tokens": 4},
                           "choices": [{"finish_reason": "stop",
                                        "message": {"content": '{"answer": "seventy-eight\\nper cent"}'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_a_model_on_a_local_openai_compatible_server_answers(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"",
        preferences_loader=lambda: {"proxy_mode": "NONE", "proxy_address": "", "request_timeout_seconds": 30},
        scratch_root=tmp_path / "transport")
    service = {"profile_ref": "local:" + "0" * 24, "endpoint_kind": "openai_compatible", "endpoint": endpoint,
               "chat_endpoint": endpoint + "/v1/chat/completions", "model_name": "gemma-3-12b", "model_digest": None,
               "exact_identity_available": True, "execution_eligible": True,
               "structured_chat_adapter": "LOCAL_OPENAI_COMPATIBLE_CHAT_V1", "connection_status": "AVAILABLE"}
    try:
        result = runner.execute_structured_chat(profile_kind="LOCAL", service=service, model={"model_name": "gemma-3-12b"},
                                                prompt="How much is recovered?", response_schema=SCHEMA,
                                                purpose="research_chat", max_output_tokens=256, timeout_seconds=30)
    finally:
        server.shutdown()
    assert result["status"] == "PASS", result
    assert result["response"] == {"answer": "seventy-eight\nper cent"}
    path, request = _Server.seen[-1]
    assert path == "/v1/chat/completions" and request["model"] == "gemma-3-12b"
