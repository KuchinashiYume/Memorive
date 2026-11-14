"""Thin, non-persistent Message Batches adapter for isolated self-owned tests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


class BatchContractError(RuntimeError):
    pass


class BatchApiError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"Message Batch API error HTTP {status}")
        self.status = status
        self.body = body


def stable_custom_id(paper_id: str, card_version: str, task: str) -> str:
    source = f"{paper_id}\0{card_version}\0{task}".encode("utf-8")
    digest = hashlib.sha256(source).hexdigest()[:32]
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", paper_id).strip("-")[:20] or "paper"
    return f"{prefix}-{task[:8]}-{digest}"[:64]


def build_batch_requests(items: list[dict[str, Any]], *, task: str) -> list[dict[str, Any]]:
    if not items:
        raise BatchContractError("batch must contain at least one item")
    built: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise BatchContractError("batch item must be an object")
        if item.get("data_ownership") != "self":
            raise BatchContractError("Message Batch is restricted to data_ownership=self")
        paper_id = item.get("paper_id")
        card_version = item.get("card_version")
        params = item.get("params")
        if not isinstance(paper_id, str) or not paper_id or not isinstance(card_version, str) or not card_version:
            raise BatchContractError("paper_id and card_version are required")
        if not isinstance(params, dict):
            raise BatchContractError("params must be a Messages request object")
        custom_id = stable_custom_id(paper_id, card_version, task)
        if custom_id in seen:
            raise BatchContractError(f"duplicate custom_id: {custom_id}")
        seen.add(custom_id)
        built.append(
            {
                "custom_id": custom_id,
                "params": dict(params),
                "metadata": {
                    "paper_id": paper_id,
                    "card_version": card_version,
                    "data_ownership": "self",
                    "task": task,
                },
            }
        )
    return built


def api_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip local evidence metadata; the provider accepts custom_id + params only."""
    return [{"custom_id": item["custom_id"], "params": item["params"]} for item in requests]


def reconcile_batch_results(
    requests: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for request in requests:
        metadata = request.get("metadata")
        if not isinstance(metadata, dict):
            raise BatchContractError("local batch metadata missing")
        recomputed = stable_custom_id(str(metadata.get("paper_id") or ""), str(metadata.get("card_version") or ""), str(metadata.get("task") or ""))
        if recomputed != request.get("custom_id"):
            raise BatchContractError("batch custom_id/card version binding mismatch")
        expected[recomputed] = request
    seen: set[str] = set()
    receipt: dict[str, dict[str, Any]] = {}
    for item in results:
        custom_id = item.get("custom_id") if isinstance(item, dict) else None
        if custom_id not in expected:
            raise BatchContractError(f"unexpected batch result custom_id: {custom_id!r}")
        if custom_id in seen:
            raise BatchContractError(f"duplicate batch result custom_id: {custom_id}")
        seen.add(custom_id)
        result = item.get("result") if isinstance(item, dict) else None
        result_type = result.get("type") if isinstance(result, dict) else "malformed"
        receipt[custom_id] = {
            "paper_id": expected[custom_id]["metadata"]["paper_id"],
            "card_version": expected[custom_id]["metadata"]["card_version"],
            "result_type": result_type,
            "card_status": "reviewed_candidate" if result_type == "succeeded" else "pending",
            "result": result,
        }
    for custom_id, request in expected.items():
        if custom_id not in seen:
            receipt[custom_id] = {
                "paper_id": request["metadata"]["paper_id"],
                "card_version": request["metadata"]["card_version"],
                "result_type": "missing",
                "card_status": "pending",
                "result": None,
            }
    return receipt


class AnthropicBatchClient:
    """One-shot HTTP helper; no scheduler, queue, recovery state, or automatic retry."""

    def __init__(
        self,
        *,
        api_key_env: str,
        api_base: str = "https://api.anthropic.com/v1",
        api_version: str = "2023-06-01",
        timeout: int = 60,
    ) -> None:
        self.api_key_env = api_key_env
        self.api_base = api_base.rstrip("/")
        self.api_version = api_version
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, jsonl: bool = False) -> tuple[Any, str | None]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise BatchContractError(f"API key env is not set: {self.api_key_env}")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{self.api_base}{path}", data=data, method=method)
        request.add_header("x-api-key", key)
        request.add_header("anthropic-version", self.api_version)
        request.add_header("accept", "application/json")
        if data is not None:
            request.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                request_id = response.headers.get("request-id")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"error": {"type": "non_json_error", "message": raw[:500]}}
            raise BatchApiError(exc.code, parsed) from exc
        if jsonl:
            return [json.loads(line) for line in raw.splitlines() if line.strip()], request_id
        return json.loads(raw), request_id

    def create(self, requests: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
        return self._request("POST", "/messages/batches", {"requests": api_requests(requests)})

    def retrieve(self, batch_id: str) -> tuple[dict[str, Any], str | None]:
        return self._request("GET", f"/messages/batches/{batch_id}")

    def results(self, batch_id: str) -> tuple[list[dict[str, Any]], str | None]:
        return self._request("GET", f"/messages/batches/{batch_id}/results", jsonl=True)

    def cancel(self, batch_id: str) -> tuple[dict[str, Any], str | None]:
        return self._request("POST", f"/messages/batches/{batch_id}/cancel", {})
