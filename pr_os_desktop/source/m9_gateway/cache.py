"""M9 Cache Layer:精确匹配、文件缓存 (库外运维区)。

- key = sha256(task_type + model_id + prompt_version + 规范化 payload);文件名 = hash、不含原始 payload。
- 默认落库外 G:\\PR-OS-运维\\缓存\\,可经环境变量 PROS_M9_CACHE_DIR 覆盖;不入 git、不进嵌入/检索。
- Phase 1 保持简单:精确匹配,无过期 / LRU / 复杂失效。只缓存成功真调结果 (调用方把关)。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DEFAULT_CACHE_DIR = r"G:\PR-OS-运维\缓存"
ENV_CACHE_DIR = "PROS_M9_CACHE_DIR"


class CacheLayer:
    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir or os.environ.get(ENV_CACHE_DIR, DEFAULT_CACHE_DIR))

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
