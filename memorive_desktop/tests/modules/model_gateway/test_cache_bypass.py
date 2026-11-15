"""Regression tests for explicit MODEL_GATEWAY cache bypass on recovery calls."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from model_gateway.cache import CacheLayer
from model_gateway.gateway import ModelGateway


class _Config:
    def slot(self, task_type: str):
        return SimpleNamespace(model_id="verify-model")


class _Prompts:
    def get_or_none(self, task_type: str):
        return None


class _Router:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, task_type: str, payload: dict) -> dict:
        self.calls += 1
        return {"text": f"fresh-{self.calls}"}


class _Fallback:
    def run(self, task_type: str, slot, operation):
        return operation(), 0


class _Cost:
    def record(self, task_type: str, result: dict, slot, prompt_version, **metadata) -> dict:
        recorded = dict(result)
        recorded.update(metadata)
        return recorded


class CacheBypassTest(unittest.TestCase):
    def test_bypass_skips_read_and_refreshes_same_cache_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m9_cache_bypass_") as temp_dir:
            gateway = object.__new__(ModelGateway)
            gateway.config = _Config()
            gateway.prompts = _Prompts()
            gateway.router = _Router()
            gateway.fallback = _Fallback()
            gateway.cost = _Cost()
            gateway.cache = CacheLayer(Path(temp_dir))
            payload = {"messages": [{"role": "user", "content": "same prompt"}]}

            first = gateway.call("verify", payload)
            cached = gateway.call("verify", payload)
            refreshed = gateway.call("verify", payload, bypass_cache=True)
            refreshed_cached = gateway.call("verify", payload)

            self.assertEqual(first["text"], "fresh-1")
            self.assertFalse(first["cache_hit"])
            self.assertEqual(cached["text"], "fresh-1")
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(refreshed["text"], "fresh-2")
            self.assertFalse(refreshed["cache_hit"])
            self.assertEqual(refreshed_cached["text"], "fresh-2")
            self.assertTrue(refreshed_cached["cache_hit"])
            self.assertEqual(gateway.router.calls, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
