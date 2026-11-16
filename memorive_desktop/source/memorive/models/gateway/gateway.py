"""The model gateway: the one way the document and research code calls a model.

Callers use call(), embed(), rerank(), ocr(), healthcheck() and region_preflight() below and never a
vendor SDK. ModelGateway adds versioned prompts (PromptRegistry), the response cache, cost records
(CostManager) and retries with error classes (FallbackPolicy) around each provider call.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from memorive.platform.logs import log_error

from memorive.models.gateway import providers
from memorive.models.gateway import region_gate
from memorive.models.gateway.cache import CacheLayer
from memorive.models.gateway.config import MODELS_YAML, GatewayConfig
from memorive.models.gateway.cost import CostManager
from memorive.models.gateway.errors import (EmbedResultInvalid, GatewayError, InvalidHealthcheckTarget,
                     OcrOwnershipBlocked, OcrResultInvalid,
                     RerankResultInvalid)
from memorive.models.gateway.fallback import FallbackPolicy, ownership_local_message
from memorive.models.gateway.prompts import PROMPT_DIR, PromptRegistry
from memorive.models.gateway.router import Router


# The one canonical name of the embedding model after normalization; the whole library is checked
# against it, and document embedding takes it from here.
CANONICAL_EMBED_MODEL = "bge-m3"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_embed_model(raw: str) -> str:
    """The embedding model's name without provider prefix or tag (BAAI/bge-m3 and bge-m3:latest both
    become bge-m3), so cloud and local bge-m3 count as the same model when the library is checked for
    one embedding model."""
    return (raw or "").split("/")[-1].split(":")[0]


def _rerank_contract_error(result, expected_count: int) -> str | None:
    """Check that a rerank reply gives each document exactly one score; returns an error summary or None.

    Only shape, indexes and numbers are checked; no query or document text is recorded. Equal counts
    are not enough: a repeated or out-of-range index would attach a score to the wrong document.
    """
    if not isinstance(result, dict):
        return f"provider 返回 {type(result).__name__}，期望对象"
    results = result.get("results")
    if not isinstance(results, list):
        return f"results 为 {type(results).__name__}，期望列表"
    if len(results) != expected_count:
        return f"输入 {expected_count} 条、返回 {len(results)} 条，数量不符"

    seen = set()
    for pos, item in enumerate(results):
        if not isinstance(item, dict):
            return f"results[{pos}] 为 {type(item).__name__}，期望对象"
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            return f"results[{pos}].index 非整数"
        if index < 0 or index >= expected_count:
            return f"results[{pos}].index={index} 越界(期望 0..{expected_count - 1})"
        if index in seen:
            return f"results[{pos}].index={index} 重复"
        seen.add(index)

        score = item.get("relevance_score")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score)):
            return f"results[{pos}].relevance_score 非有限数值"

    if seen != set(range(expected_count)):
        return "results 的 index 未完整覆盖输入 documents"
    return None


def _ocr_contract_error(
    result,
    *,
    page_number: int,
    source_sha256: str,
) -> str | None:
    """The minimal shape of one page's OCR result; returns a structural error summary, never the text."""
    if not isinstance(result, dict):
        return f"provider 返回 {type(result).__name__}，期望对象"
    if not isinstance(result.get("text"), str) or not result["text"].strip():
        return "text 缺失或为空"
    if result.get("page_number") != page_number:
        return f"page_number={result.get('page_number')!r}，期望 {page_number}"
    if result.get("source_sha256") != source_sha256:
        return "source_sha256 未回显或与请求不符"
    response_sha256 = result.get("response_sha256")
    if not isinstance(response_sha256, str) or not _SHA256_RE.fullmatch(response_sha256):
        return "response_sha256 缺失或不是 64 位小写十六进制"
    return None

_CONFIG_PATH = MODELS_YAML
_PROMPTS_ROOT = PROMPT_DIR


class ModelGateway:
    def __init__(self, config_path: Path = _CONFIG_PATH, prompts_root: Path = _PROMPTS_ROOT, *, region_probe=None):
        self.config = GatewayConfig(config_path)
        self.router = Router(self.config)
        self.prompts = PromptRegistry(prompts_root)
        self.cache = CacheLayer()                     # a file cache outside the library
        self.cost = CostManager(self.config)          # writes cost records to the runtime log
        self.fallback = FallbackPolicy()              # retries and the four error classes
        self._region_probe = region_probe

    def _require_region_authorization(self, task_type: str, slot) -> dict:
        # Some established offline tests construct a minimal gateway via __new__.
        # Absence of the injected probe must still use the default fail-closed path.
        receipt = region_gate.preflight_for_slot(
            slot, probe=getattr(self, "_region_probe", None)
        )
        if not receipt.get("ok") or receipt.get("gated"):
            raise GatewayError(receipt.get("message") or f"[{task_type}] 区域证据未获授权；请求已在发送前挡下。")
        return receipt

    def _dispatch_with_region_receipt(self, task_type: str, slot, receipt: dict, payload: dict, *, capability: str = "chat"):
        if not region_gate.validate_dispatch_receipt(slot, receipt):
            raise GatewayError(f"[{task_type}] 区域出口绑定在 dispatch 前失效；请求已挡下。")
        if capability == "chat":
            return self.router.dispatch(task_type, payload)
        return self.router.dispatch(task_type, payload, capability=capability)

    def call(self, task_type: str, payload: dict, *, bypass_cache: bool = False) -> dict:
        # The module's current template from the registry (None if there is none); its version goes
        # into the cache key and the cost record.
        template = self.prompts.get_or_none(task_type)
        # As a string ("v2", like prompts/<module>/v<N>.md): the cost record takes str or None,
        # PromptRegistry.version is an int.
        prompt_version = f"v{template.version}" if template else None
        slot = self.config.slot(task_type)   # api_key_env becomes quota_account, model_id goes into the cache key
        key = self.cache.key(task_type, slot.model_id, prompt_version, payload)

        # A cache hit returns without a call (a cost record with cache_hit=true is still written, at no
        # cost). A disaster-recovery retry can bypass the cached reply; it keeps the same key and a
        # successful call refreshes the cache, so a bad reply is not replayed again.
        if not bypass_cache:
            hit = self.cache.get(key)
            if hit is not None:
                return self.cost.record(task_type, hit, slot, prompt_version, cache_hit=True)

        # Only when a real request is about to be sent; a cache hit sends nothing.
        region_receipt = self._require_region_authorization(task_type, slot)

        # FallbackPolicy wraps the request: transient errors are retried, configuration errors stop it,
        # every failure is logged.
        result, retries = self.fallback.run(
            task_type, slot, lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, payload))
        result["prompt_version"] = prompt_version
        if result.get("text"):               # only successful, non-empty replies are cached
            self.cache.put(key, result)
        # The cost record (cache_hit=false; retries come from FallbackPolicy).
        return self.cost.record(task_type, result, slot, prompt_version, retries=retries, cache_hit=False)

    # ── embeddings: the same slot, retries and cost record as call() ──
    def embed(self, task_type: str, inputs: list) -> dict:
        """Embed inputs, a list of texts (document embedding passes one chunk at a time). Unlike call()
        there is no prompt template and no cache, so vectors of someone else's data never enter a shared
        cache. Returns the embeddings, the normalized embedding_model and the raw provider_model_id."""
        slot = self.config.slot(task_type)
        region_receipt = self._require_region_authorization(task_type, slot)
        result, retries = self.fallback.run(
            task_type, slot,
            lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, {"input": inputs}, capability="embed"))
        raw_id = result.get("model") or slot.model_id
        result["provider_model_id"] = raw_id                   # as the API returned it (BAAI/bge-m3 or bge-m3:latest)
        result["embedding_model"] = _canonical_embed_model(raw_id)   # cloud and local both bge-m3
        # The count is checked before the cost record: N inputs must give N vectors. Otherwise the error
        # is logged and EmbedResultInvalid raised, and no success is recorded.
        n_in, n_out = len(inputs), len(result.get("embeddings") or [])
        if n_out != n_in:
            log_error("MODEL_GATEWAY", "嵌入返回数量不符,已拒用", context={
                "step": f"embed({task_type}) 数量校验",
                "error": f"输入 {n_in} 条、返回 {n_out} 条,数量不符;拒用、不记成功账、不写向量(防 chunk↔向量错位)",
                "task_type": task_type,
                "dependency": f"{slot.provider}/{slot.model_id}"})
            raise EmbedResultInvalid(
                f"[{task_type}] 嵌入返回数量不符:输入 {n_in} 条、返回 {n_out} 条;"
                f"已拒用(不记成功账、一条不写、防 chunk↔向量错位)。")
        return self.cost.record(                               # embed_cloud and embed_local are recorded as embed
            task_type, result, slot, None, retries=retries, cache_hit=False)

    # ── reranking: like embed(), with capability=rerank ──
    def rerank(self, task_type: str, query: str, documents: list) -> dict:
        """Rerank documents (candidate chunk texts) against query. Returns results: [{index,
        relevance_score}], the indexes referring to documents. Like embed(): no prompt template and no
        cache. SiliconFlow is a domestic provider, so the region check skips it."""
        slot = self.config.slot(task_type)
        region_receipt = self._require_region_authorization(task_type, slot)
        result, retries = self.fallback.run(
            task_type, slot,
            lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, {"query": query, "documents": documents}, capability="rerank"))
        # Checked before the cost record: each input index exactly once and every score valid; with N
        # results but a repeated index a score would attach to the wrong chunk. No success is recorded
        # for a bad reply.
        n_in = len(documents)
        contract_error = _rerank_contract_error(result, n_in)
        if contract_error is not None:
            log_error("MODEL_GATEWAY", "重排返回契约不符,已拒用", context={
                "step": f"rerank({task_type}) 契约校验",
                "error": f"{contract_error};拒用、不记成功账(防 block↔score 错位)",
                "task_type": task_type,
                "dependency": f"{slot.provider}/{slot.model_id}"})
            raise RerankResultInvalid(
                f"[{task_type}] 重排返回契约不符:{contract_error};"
                f"已拒用(不记成功账、防 block↔score 错位)。")
        result["results"] = sorted(result["results"], key=lambda item: item["index"])
        return self.cost.record(                               # task_type rerank
            task_type, result, slot, None, retries=retries, cache_hit=False)

    def ocr(
        self,
        task_type: str,
        image_bytes: bytes,
        *,
        mime_type: str,
        page_number: int,
        source_sha256: str,
        data_ownership: str,
    ) -> dict:
        """OCR of one page; no shared cache. Data ownership is checked before the request and the
        result's shape after it."""
        if data_ownership != "self":
            raise OcrOwnershipBlocked(
                f"[{task_type}] data_ownership={data_ownership!r}；"
                "受托数据禁止编码、上传或调用远程 OCR。"
            )
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise OcrResultInvalid(f"[{task_type}] image_bytes 必须是非空 bytes。")
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
            raise OcrResultInvalid(f"[{task_type}] page_number 必须是从 1 开始的整数。")
        if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
            raise OcrResultInvalid(f"[{task_type}] source_sha256 必须是 64 位小写十六进制。")
        if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
            raise OcrResultInvalid(f"[{task_type}] mime_type 必须是 image/*。")

        slot = self.config.slot(task_type)
        region_receipt = self._require_region_authorization(task_type, slot)
        template = self.prompts.get_or_none(task_type, slot.prompt_version)
        if template is None or not template.body.strip():
            raise OcrResultInvalid(f"[{task_type}] 缺少版本化 OCR Prompt；拒绝使用隐式提示词。")
        prompt_version = f"v{template.version}"
        payload = {
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "page_number": page_number,
            "source_sha256": source_sha256,
            "prompt": template.body,
        }
        result, retries = self.fallback.run(
            task_type,
            slot,
            lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, payload, capability="ocr"),
        )
        contract_error = _ocr_contract_error(
            result,
            page_number=page_number,
            source_sha256=source_sha256,
        )
        if contract_error is not None:
            log_error("MODEL_GATEWAY", "OCR 返回契约不符,已拒用", context={
                "step": f"ocr({task_type}) 契约校验",
                "error": f"{contract_error};拒用、不记成功账",
                "task_type": task_type,
                "page_number": str(page_number),
                "dependency": f"{slot.provider}/{slot.model_id}",
            })
            raise OcrResultInvalid(
                f"[{task_type}] OCR 返回契约不符:{contract_error};已拒用且不记成功账。"
            )
        result["prompt_version"] = prompt_version
        result.pop("raw", None)
        return self.cost.record(
            task_type,
            result,
            slot,
            prompt_version,
            retries=retries,
            cache_hit=False,
        )

    # ── local embedding readiness: only embed_local; document embedding stops when not ready ──
    def healthcheck(self, task_type: str) -> dict:
        """Whether the local embedding model is ready (GET /api/tags; no text is sent). Returns {ready,
        ...}; when not ready, a message about data ownership is added, which document embedding logs when
        it stops. Only embed_local: the cloud slot needs no such check, and its message would be wrong
        there, so other slots are refused."""
        if task_type != "embed_local":
            raise InvalidHealthcheckTarget(
                f"healthcheck 仅用于 embed_local 安全阀预检,不适用于 {task_type!r};"
                f"自有数据走云端无「绝不降级」红线、不需预检、不套归属保护文案。")
        slot = self.config.slot(task_type)
        if not slot.enabled:
            reason = f"槽 {task_type} 未启用(enabled=false)"
            return {"ready": False, "reason": reason,
                    "message": ownership_local_message(task_type, slot, reason)}
        adapter = providers.get_adapter(slot.provider)
        try:
            hc = adapter.healthcheck(slot)                     # unreachable, timeout or bad reply: not ready
        except Exception as exc:
            hc = {"ready": False, "reason": f"本地服务不可达({type(exc).__name__})", "want": slot.model_id}
        if hc.get("ready"):
            return hc
        hc.setdefault("reason", "未就绪")
        hc["message"] = ownership_local_message(task_type, slot, hc["reason"])
        return hc

    # ── the region check before an overseas API: look up the slot, then decide in region_gate ──
    def region_preflight(self, task_type: str, *, probe=None) -> dict:
        """Before calling an overseas API (GPT, Gemini, Claude), look up this computer's exit region:
        mainland China stops the call, elsewhere lets it through, an unknown region is left to the user.
        Only the exit region is probed; no API request is made. region_gate.py states the two premises
        (it holds only behind a global TUN proxy; a passed region does not mean the key works).
        Domestic, local, human and placeholder slots are skipped. probe can be injected for tests."""
        slot = self.config.slot(task_type)    # an unknown task_type raises UnknownTaskType
        return region_gate.preflight_for_slot(
            slot, probe=probe or getattr(self, "_region_probe", None)
        )


from contextvars import ContextVar
from contextlib import contextmanager

_default: ModelGateway | None = None
_scoped_gateway = ContextVar("memo_task_gateway", default=None)

@contextmanager
def gateway_scope(gateway):
    token = _scoped_gateway.set(gateway)
    try:
        yield
    finally:
        _scoped_gateway.reset(token)

def current_gateway():
    scoped = _scoped_gateway.get()
    if scoped is not None:
        return scoped
    global _default
    if _default is None:
        _default = ModelGateway()
    return _default


def call(task_type: str, payload: dict, *, bypass_cache: bool = False) -> dict:
    """A chat call. Usage: from memorive.models.gateway import call; call("distill", {...})"""
    return current_gateway().call(task_type, payload, bypass_cache=bypass_cache)


def embed(task_type: str, inputs: list) -> dict:
    """An embedding call (document embedding): from memorive.models.gateway import embed; embed("embed_cloud", [text, ...])"""
    return current_gateway().embed(task_type, inputs)


def rerank(task_type: str, query: str, documents: list) -> dict:
    """A rerank call (candidate retrieval): from memorive.models.gateway import rerank; rerank("rerank", question, [text, ...])
       returns {results: [{index, relevance_score}], ...}, the indexes referring to documents."""
    return current_gateway().rerank(task_type, query, documents)


def ocr(
    task_type: str,
    image_bytes: bytes,
    *,
    mime_type: str,
    page_number: int,
    source_sha256: str,
    data_ownership: str,
) -> dict:
    """OCR of one page image; callers go through the gateway, never to a provider directly."""
    return current_gateway().ocr(
        task_type,
        image_bytes,
        mime_type=mime_type,
        page_number=page_number,
        source_sha256=source_sha256,
        data_ownership=data_ownership,
    )


def healthcheck(task_type: str) -> dict:
    """Whether the local embedding model is ready (document embedding): healthcheck("embed_local") returns {ready, message, ...}"""
    return current_gateway().healthcheck(task_type)


def region_preflight(task_type: str, *, probe=None) -> dict:
    """The region check before calling an overseas API:
        from memorive.models.gateway import region_preflight
        rc = region_preflight("analysis")   # -> {ok, gated, region, reason, message, ...}
    rc['gated'] says whether to stop, rc['reason'] why; region_gate.py's docstring states its two premises."""
    return current_gateway().region_preflight(task_type, probe=probe)
