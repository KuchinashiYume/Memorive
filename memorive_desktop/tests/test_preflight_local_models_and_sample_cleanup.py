from __future__ import annotations

from pathlib import Path
import sys


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from runtime import ProductApi  # noqa: E402
from memorive_local_models.product_controller import LocalModelProductController  # noqa: E402


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ollama_projection_preserves_exact_digest_for_phase7_adapter(tmp_path: Path) -> None:
    digest = "a" * 64
    details = LocalModelProductController._model_details_from_payload(
        "ollama",
        {"models": [{"name": "qwen3:8b", "digest": digest}]},
    )
    assert details == [{"model_name": "qwen3:8b", "model_digest": digest.upper()}]

    controller = LocalModelProductController(tmp_path / "local-models")
    state = controller._load()
    endpoint_id = next(
        key for key, value in state["endpoints"].items() if value["kind"] == "ollama"
    )
    state["endpoints"][endpoint_id]["last_verification"] = {
        "endpoint_id": endpoint_id,
        "verified_at": "2026-08-29T00:00:00Z",
        "service_reachable": True,
        "model_listed": True,
        "models": ["qwen3:8b"],
        "model_details": details,
        "inference_run": False,
        "status": "MODEL_LISTED_INFERENCE_NOT_RUN",
    }
    controller._save(state)

    model = controller.call("local_models.list", {})["recognized_models"][0]
    assert model["display_name"] == "[本地] qwen3:8b"
    assert model["chat_endpoint"] == "http://127.0.0.1:11434/api/chat"
    assert model["model_digest"] == digest.upper()
    assert model["structured_chat_adapter"] == "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
    assert model["execution_eligible"] is True


