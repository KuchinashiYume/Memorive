"""A model the official API names differently from the reference sources still finds its reference scores.

DeepSeek's API names its flash model deepseek-flash; Epoch AI and LiveBench list it as deepseek-v4-flash.
Pricing already treats the two as one model, but the settings page's step panel found no reference for
deepseek-flash. The reference lookup now tries both names; an unrelated name still finds nothing.
"""
from __future__ import annotations

from memorive.desktop.settings.external_sources import normalize_payload, role_references
from leaderboard_test_support import LIVEBENCH_SOURCE
from test_livebench import bundle


def test_the_official_name_finds_the_reference_of_the_listed_name():
    rows = normalize_payload(LIVEBENCH_SOURCE, bundle([("deepseek-v4-flash", 60), ("qwen3.8-27b", 70)]))
    [reference] = role_references("ANALYSIS_PRIMARY", {"model_name": "deepseek-flash"}, rows)["references"]
    assert reference["model_slug"] == "deepseek-v4-flash"
    assert role_references("ANALYSIS_PRIMARY", {"model_name": "deepseek-chat"}, rows)["references"] == []
