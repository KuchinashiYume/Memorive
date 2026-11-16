from __future__ import annotations

import io
import json
from pathlib import Path
import sys


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.models.gateway.local_structured_chat import LocalStructuredChatAdapter  # noqa: E402


DIGEST = "25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e"
MODEL = "qwen3.8:27b-q4_K_M"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def binding(endpoint: str = "http://127.0.0.1:11434/api/chat", digest: str = DIGEST) -> dict:
    return {
        "schema_version": "EvaluationAssetsLocalStructuredChatBinding-v1",
        "endpoint": endpoint,
        "requested_model": MODEL,
        "expected_model_digest": digest,
        "think": False,
        "temperature": 0,
        "seed": 20260822,
        "num_ctx": 16384,
        "num_predict": 1024,
        "cloud_fallback": False,
    }


def fake_transport(*, returned_model: str = MODEL, content: str | None = None, digest: str = DIGEST):
    calls: list[tuple[str, str]] = []
    posts: list[dict] = []

    def open_request(request, *, timeout):
        calls.append((request.full_url, request.get_method()))
        if request.full_url.endswith("/api/tags"):
            payload = {"models": [{"model": MODEL, "digest": digest}]}
        elif request.full_url.endswith("/api/version"):
            payload = {"version": "0.32.14"}
        else:
            posts.append(json.loads(request.data.decode("utf-8")))
            payload = {
                "model": returned_model,
                "message": {"content": content if content is not None else '{"answer":"ok"}'},
                "prompt_eval_count": 10,
                "eval_count": 3,
            }
        return _Response(json.dumps(payload).encode("utf-8"))

    return open_request, calls, posts


def test_pass_binds_identity_parameters_usage_and_zero_egress() -> None:
    transport, calls, posts = fake_transport()
    outcome = LocalStructuredChatAdapter(urlopen=transport).execute(
        prompt="Return the bounded synthetic answer.",
        schema=SCHEMA,
        binding=binding(),
        purpose="DETERMINISTIC_TEST",
    )
    assert outcome.success
    assert outcome.response == {"answer": "ok"}
    assert outcome.receipt["model_digest"] == DIGEST.upper()
    assert outcome.receipt["ollama_version"] == "0.32.14"
    assert outcome.receipt["external_request_count"] == 0
    assert outcome.receipt["api_cost_cny"] == 0.0
    assert [method for _, method in calls] == ["GET", "GET", "POST"]
    assert posts[0]["think"] is False
    assert posts[0]["keep_alive"] == 0
    assert posts[0]["options"]["temperature"] == 0


def test_non_loopback_is_blocked_before_any_request() -> None:
    transport, calls, _ = fake_transport()
    outcome = LocalStructuredChatAdapter(urlopen=transport).execute(
        prompt="Synthetic only.",
        schema=SCHEMA,
        binding=binding("https://example.com/api/chat"),
        purpose="DETERMINISTIC_TEST",
    )
    assert not outcome.success
    assert outcome.error_code == "NON_LOOPBACK_OR_UNEXPECTED_ENDPOINT"
    assert calls == []


def test_ordinary_words_such_as_gold_are_sent_as_written() -> None:
    # Research text may say gold, scorer or answer key; the local channel does not refuse words.
    prompt = "Compare gold nanoparticles with the gold standard; the scorer and answer key are in the appendix."
    for purpose in ("research_chat", "conversation_refinement", "DETERMINISTIC_TEST"):
        transport, calls, posts = fake_transport()
        outcome = LocalStructuredChatAdapter(urlopen=transport).execute(
            prompt=prompt,
            schema=SCHEMA,
            binding=binding(),
            purpose=purpose,
        )
        assert outcome.success
        assert posts[0]["messages"][0]["content"] == prompt


def test_digest_drift_blocks_model_request() -> None:
    transport, calls, _ = fake_transport(digest="0" * 64)
    outcome = LocalStructuredChatAdapter(urlopen=transport).execute(
        prompt="Synthetic only.", schema=SCHEMA, binding=binding(), purpose="DETERMINISTIC_TEST"
    )
    assert not outcome.success
    assert outcome.error_code == "MODEL_IDENTITY_MISMATCH"
    assert calls == [("http://127.0.0.1:11434/api/tags", "GET")]
    assert outcome.receipt["local_model_request_count"] == 0


def test_returned_model_mismatch_invalidates_response() -> None:
    transport, _, _ = fake_transport(returned_model="different:model")
    outcome = LocalStructuredChatAdapter(urlopen=transport).execute(
        prompt="Synthetic only.", schema=SCHEMA, binding=binding(), purpose="DETERMINISTIC_TEST"
    )
    assert not outcome.success
    assert outcome.error_code == "RETURNED_MODEL_MISMATCH"
    assert outcome.receipt["local_model_request_count"] == 1


def test_malformed_or_schema_invalid_content_is_fail_closed() -> None:
    malformed, _, _ = fake_transport(content="not-json")
    first = LocalStructuredChatAdapter(urlopen=malformed).execute(
        prompt="Synthetic only.", schema=SCHEMA, binding=binding(), purpose="DETERMINISTIC_TEST"
    )
    assert not first.success
    assert first.error_code == "MODEL_CONTENT_JSON_INVALID"

    invalid, _, _ = fake_transport(content='{"wrong":"field"}')
    second = LocalStructuredChatAdapter(urlopen=invalid).execute(
        prompt="Synthetic only.", schema=SCHEMA, binding=binding(), purpose="DETERMINISTIC_TEST"
    )
    assert not second.success
    assert second.error_code == "MODEL_CONTENT_SCHEMA_INVALID"
