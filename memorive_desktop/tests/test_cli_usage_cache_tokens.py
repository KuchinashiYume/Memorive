"""Input tokens of a Claude Code request include the cache (models/validation/usage.py).

Claude Code's result line carries usage and, after it, modelUsage, whose cache counts are in camelCase
(cacheReadInputTokens, cacheCreationInputTokens). The last group was taken and the camelCase cache counts were
not added to the input, so an answer with some 5,300 input tokens was recorded with 10.
"""
from __future__ import annotations

import json

from memorive.models.validation.usage import _normalise_token_usage, _usage_from_cli_output

RESULT = {"type": "result", "usage": {"input_tokens": 10, "cache_read_input_tokens": 5000,
                                       "cache_creation_input_tokens": 294, "output_tokens": 406},
          "modelUsage": {"claude-haiku-5-5": {"inputTokens": 10, "outputTokens": 406, "cacheReadInputTokens": 5000,
                                              "cacheCreationInputTokens": 294}}}


def test_the_cache_counts_in_camel_case_are_part_of_the_input():
    usage = _normalise_token_usage(_usage_from_cli_output(json.dumps(RESULT).encode()))
    assert usage["prompt_tokens"] == 5304 and usage["completion_tokens"] == 406
    assert (usage["cached_input_tokens"], usage["cache_write_input_tokens"]) == (5000, 294)
