"""The built-in local model services (Ollama, LM Studio / OpenAI compatible, llama.cpp) stay in the list.

A service added at a built-in's address (Ollama at http://127.0.0.1:11434) replaced the built-in entry;
removing it then removed Ollama from the list for good, and "discover models" found nothing. Now such an
address is refused as already listed, and a state file that lost a built-in gets it back.
"""
from __future__ import annotations

import json

import pytest

from memorive.models.local.product_controller import LocalModelProductController, LocalModelProductError


def _kinds(controller):
    return sorted(row["kind"] for row in controller._load()["endpoints"].values())


def test_a_built_in_address_is_refused_as_already_listed(tmp_path):
    controller = LocalModelProductController(tmp_path)
    with pytest.raises(LocalModelProductError, match="LOCAL_MODEL_ENDPOINT_ALREADY_LISTED"):
        controller.call("local_models.endpoint_save",
                        {"display_name": "My Ollama", "kind": "ollama", "endpoint": "http://127.0.0.1:11434"})
    assert _kinds(controller) == ["llama_cpp", "ollama", "openai_compatible"]


def test_a_built_in_lost_from_the_state_file_comes_back(tmp_path):
    LocalModelProductController(tmp_path)
    path = tmp_path / "local_models_v1.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["endpoints"] = {key: row for key, row in state["endpoints"].items() if row["kind"] != "ollama"}
    path.write_text(json.dumps(state), encoding="utf-8")
    assert _kinds(LocalModelProductController(tmp_path)) == ["llama_cpp", "ollama", "openai_compatible"]
