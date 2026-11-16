"""The model gateway's response cache: exact-match files in the ops area.

- key = sha256(task_type + model_id + prompt_version + normalized payload); the file name is the
  hash and holds no raw payload.
- The folder is MEMORIVE_MODEL_GATEWAY_CACHE_DIR, or model_gateway_cache in the ops area
  (memorive/platform/data_folders.py, ops_root). It is never committed, embedded or searched.
- Kept simple: exact match only, no expiry, LRU or invalidation. Only successful real calls are
  cached; the caller decides that.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from memorive.platform.data_folders import ops_root

ENV_CACHE_DIR = "MEMORIVE_MODEL_GATEWAY_CACHE_DIR"


class CacheLayer:
    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir or os.environ.get(ENV_CACHE_DIR) or ops_root() / "model_gateway_cache")

    def key(self, task_type: str, model_id: str | None, prompt_version: str | None, payload: dict) -> str:
        canon = json.dumps(
            {"task_type": task_type, "model_id": model_id,
             "prompt_version": prompt_version, "payload": payload},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"        # 文件名 = hash,不写原始 payload

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None                              # 坏缓存视作未命中

    def put(self, key: str, result: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
