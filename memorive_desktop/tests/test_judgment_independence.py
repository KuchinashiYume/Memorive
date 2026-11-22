"""Step 08 (the analysis cross-check) with the model the user chose, and how independent that check is.

The user decided (2026-11-22) that the checking steps may use the model they check. Step 08 used to send no
request when its model was of the analysis model's vendor, or of a vendor not recognised (Kimi, GLM,
MiniMax, Grok), while the step and the task page said "completed". Now it runs with the chosen model and
records how independent the check was; the human review package says so when it is not another vendor.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from memorive.documents.workflow.model_bridge import CoreModelBridge, _provider_family


@pytest.mark.parametrize("names, family", [
    (("Moonshot", "kimi-k2.6"), "moonshot"), (("智谱", "glm-5"), "zhipu"), (("", "minimax-text"), "minimax"),
    (("xAI", "grok-4"), "xai"), (("DeepSeek", "deepseek-v4-pro"), "deepseek"),
])
def test_the_vendor_of_these_models_is_recognised(names, family):
    assert _provider_family(*names) == family


def _bridge(provider, model):
    node = {"provider": provider, "profile_ref": "api:checker", "execution_profile": {"model": {"model_name": model}}}
    return SimpleNamespace(_node=lambda node_id: node)


@pytest.mark.parametrize("provider, model, independence", [
    ("Anthropic", "claude-sonnet-5", "OTHER_VENDOR"),
    ("DeepSeek", "deepseek-flash", "SAME_VENDOR"),
    ("DeepSeek", "deepseek-v4-pro", "SAME_MODEL"),
    ("Some service", "my-model", "VENDOR_UNKNOWN"),
])
def test_the_chosen_model_checks_and_the_independence_is_named(provider, model, independence):
    bridge = _bridge(provider, model)
    _producer, [candidate] = CoreModelBridge.select_judgment_candidates(bridge, "deepseek-v4-pro")
    assert candidate.model_id == model
    assert CoreModelBridge.judgment_independence(bridge, "deepseek-v4-pro") == independence
