"""M9 统一入口。业务模块只 import 本模块的 call(),绝不 import 任何厂商 SDK。

四个能力(Prompt 版本化 / 缓存 / 记账 / 降级)本步只留挂接位,
后续步骤在 call() 里按标注接上,骨架不用重构。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from m11_log import log_error

from . import providers
from . import region_gate
from .cache import CacheLayer
from .config import GatewayConfig
from .cost import CostManager
from .errors import (EmbedResultInvalid, GatewayError, InvalidHealthcheckTarget,
                     OcrOwnershipBlocked, OcrResultInvalid,
                     RerankResultInvalid)
from .fallback import FallbackPolicy, ownership_local_message
from .prompts import PromptRegistry
from .router import Router


# 本系统嵌入模型归一化后的**唯一 canonical 名**(单一来源:整库一致性基准;M1e 承带、不自定义,承 T3 第 3 步对抗审查 #8)。
CANONICAL_EMBED_MODEL = "bge-m3"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_embed_model(raw: str) -> str:
    """归一化嵌入模型名:去 provider 前缀(BAAI/bge-m3→bge-m3)+ 去 :tag(bge-m3:latest→bge-m3)。
    使云 / 本地同一 bge-m3 记同名,整库重嵌一致性检查不把云 / 本地误判成模型冲突挡下(承 T3 第 3 步)。"""
    return (raw or "").split("/")[-1].split(":")[0]


def _rerank_contract_error(result, expected_count: int) -> str | None:
    """校验 rerank 的 score↔document 对齐契约，返回错误摘要或 None。

    只校验形状、index 与数值，不记录 query/document 原文。数量相等仍不足以防止
    重复或越界 index 让某个 score 静默错挂到另一块上。
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
    """单页 OCR 结果最小契约；只返回结构错误摘要，不含识别正文。"""
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

_CONFIG_PATH = Path(__file__).with_name("models.yaml")    # config 紧挨 code
_PROMPTS_ROOT = Path(__file__).with_name("prompts")       # Prompt 模板库(第2步)


class ModelGateway:
    def __init__(self, config_path: Path = _CONFIG_PATH, prompts_root: Path = _PROMPTS_ROOT, *, region_probe=None):
        self.config = GatewayConfig(config_path)
        self.router = Router(self.config)
        self.prompts = PromptRegistry(prompts_root)   # 第2步:Prompt Registry
        self.cache = CacheLayer()                     # 第3步:Cache Layer (文件缓存、库外)
        self.cost = CostManager(self.config)          # 第3步:Cost & Quota Manager (记账写 M11)
        self.fallback = FallbackPolicy()              # 第4步:Fallback + 四类报错
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
        # [第2步] 从 Registry 取该模块当前模板(取不到不炸);版本供缓存 key 与记账用。
        template = self.prompts.get_or_none(task_type)
        # 规范成字符串版本号 (如 "v2",与 prompts/<module>/v<N>.md 命名一致);
        # M11 记账契约要 str/None,PromptRegistry.version 是 int,在此统一口径。
        prompt_version = f"v{template.version}" if template else None
        slot = self.config.slot(task_type)   # api_key_env→quota_account、model_id→缓存 key
        key = self.cache.key(task_type, slot.model_id, prompt_version, payload)

        # [第3步] 缓存命中:不真调、直接返回(仍记一条 cache_hit=true 记账,不重复计费)。
        # 灾难恢复重试可显式绕过旧响应；仍沿用同一 key，真调成功后刷新缓存，避免后续继续重放坏响应。
        if not bypass_cache:
            hit = self.cache.get(key)
            if hit is not None:
                return self.cost.record(task_type, hit, slot, prompt_version, cache_hit=True)

        # 仅在将要发生真实 dispatch 时执行；缓存命中不产生外部请求。
        region_receipt = self._require_region_authorization(task_type, slot)

        # [第4步] Fallback 包住 dispatch:瞬时错重试、配置类分类挡下、每次失败 M11 留痕、不崩
        result, retries = self.fallback.run(
            task_type, slot, lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, payload))
        result["prompt_version"] = prompt_version
        if result.get("text"):               # 只缓存成功、非空真调结果(失败已抛受控 DependencyError、空返回不缓存)
            self.cache.put(key, result)
        # [第3步] 记账写 M11(cache_hit=false;retries 由 Fallback 提供)
        return self.cost.record(task_type, result, slot, prompt_version, retries=retries, cache_hit=False)

    # ── 嵌入(T3 第 3 步)——call() 的语义包装,复用同一核心(同槽/同 fallback/同记账),非旁路 ──
    def embed(self, task_type: str, inputs: list) -> dict:
        """嵌入入口。inputs = 待嵌文本列表(M1e 逐块传 [text])。
        与 call() 唯一差别:嵌入无 Prompt 模板、不走缓存(加固:防他人数据向量进共享缓存)——
        二者对嵌入本就 N/A。返回含 embeddings + 归一化 embedding_model + provider_model_id(raw)。"""
        slot = self.config.slot(task_type)                     # 同一槽配置
        region_receipt = self._require_region_authorization(task_type, slot)
        result, retries = self.fallback.run(                   # 同一 Fallback(重试/挡下/留痕)
            task_type, slot,
            lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, {"input": inputs}, capability="embed"))
        raw_id = result.get("model") or slot.model_id
        result["provider_model_id"] = raw_id                   # 审计:API 回显 raw(BAAI/bge-m3 或 bge-m3:latest)
        result["embedding_model"] = _canonical_embed_model(raw_id)   # 归一化:云/本地同记 bge-m3
        # 数量校验**先于记账**:输入 N → 必须返回 N。不符 → 留 M11 错误痕 + 抛 EmbedResultInvalid,
        # **绝不记成功账**(否则污染第③场景"M9 记账无 cloud embed"硬证据、且成功假象误导);校验过才 record。
        n_in, n_out = len(inputs), len(result.get("embeddings") or [])
        if n_out != n_in:
            log_error("M9", "嵌入返回数量不符,已拒用", context={
                "step": f"embed({task_type}) 数量校验",
                "error": f"输入 {n_in} 条、返回 {n_out} 条,数量不符;拒用、不记成功账、不写向量(防 chunk↔向量错位)",
                "task_type": task_type,
                "dependency": f"{slot.provider}/{slot.model_id}"})
            raise EmbedResultInvalid(
                f"[{task_type}] 嵌入返回数量不符:输入 {n_in} 条、返回 {n_out} 条;"
                f"已拒用(不记成功账、一条不写、防 chunk↔向量错位)。")
        return self.cost.record(                               # 同一记账(cost 已认 embed_cloud/local→embed+backend)
            task_type, result, slot, None, retries=retries, cache_hit=False)

    # ── 重排(A·修跨篇混入)——embed() 的姊妹件,同槽/同 fallback/同记账,只多 capability=rerank 一路 ──
    def rerank(self, task_type: str, query: str, documents: list) -> dict:
        """重排入口。query = 问题串;documents = 候选块原文列表。回 results:[{index, relevance_score}](按 index 对齐 documents)。
        与 embed() 同:无 Prompt 模板、**不走缓存**(防他人数据进共享缓存,rerank 亦 N/A)。
        siliconflow(国内)→ region_gate domestic_skip,无需 region_preflight。"""
        slot = self.config.slot(task_type)                     # 同一槽配置
        region_receipt = self._require_region_authorization(task_type, slot)
        result, retries = self.fallback.run(                   # 同一 Fallback(重试/挡下/留痕)
            task_type, slot,
            lambda: self._dispatch_with_region_receipt(task_type, slot, region_receipt, {"query": query, "documents": documents}, capability="rerank"))
        # 契约校验**先于记账**:不仅数量相同，还必须 index 唯一、完整覆盖输入且 score 合法。
        # 否则哪怕 N 条都在，也可能把某块的分数静默错挂到另一块，绝不记成功账。
        n_in = len(documents)
        contract_error = _rerank_contract_error(result, n_in)
        if contract_error is not None:
            log_error("M9", "重排返回契约不符,已拒用", context={
                "step": f"rerank({task_type}) 契约校验",
                "error": f"{contract_error};拒用、不记成功账(防 block↔score 错位)",
                "task_type": task_type,
                "dependency": f"{slot.provider}/{slot.model_id}"})
            raise RerankResultInvalid(
                f"[{task_type}] 重排返回契约不符:{contract_error};"
                f"已拒用(不记成功账、防 block↔score 错位)。")
        result["results"] = sorted(result["results"], key=lambda item: item["index"])
        return self.cost.record(                               # 同一记账(task_type=rerank,M11 enum 已认)
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
        """页面级 OCR；不走共享缓存，所有权与结果契约分别在网络前后硬校验。"""
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
            log_error("M9", "OCR 返回契约不符,已拒用", context={
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

    # ── 嵌入安全阀健康探测(T3 第 3 步)——仅供 embed_local;M9 报告状态,M1e 据 ready 决定挡下 ──
    def healthcheck(self, task_type: str) -> dict:
        """纯能力探测本地嵌入是否就绪(GET /api/tags,不发任何待嵌文本)。
        返回结构化 {ready, ...};未就绪时补归属保护 message(M9 给,M1e 挡下时记 M11 detail)。
        **硬限制:仅 embed_local**——自有数据走云端无「绝不降级」红线、不需预检,
        误调 embed_cloud 会把归属保护文案套到云端(语义错),故受控报错挡下。"""
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
            hc = adapter.healthcheck(slot)                     # 纯探测(连不上/超时/解析失败均视未就绪、不崩)
        except Exception as exc:
            hc = {"ready": False, "reason": f"本地服务不可达({type(exc).__name__})", "want": slot.model_id}
        if hc.get("ready"):
            return hc
        hc.setdefault("reason", "未就绪")
        hc["message"] = ownership_local_message(task_type, slot, hc["reason"])   # 补友好文案
        return hc

    # ── 境外 API 区域预检门(测前安全阀)——call() 的姊妹件,同「查 slot → 交纯逻辑」结构 ──
    def region_preflight(self, task_type: str, *, probe=None) -> dict:
        """测境外 API(GPT/Gemini/Claude)前查本机出口区域:大陆挡下、非陆放行、查不到交人。
        只探测出口归属、**不发起任何 API 请求**;与 M6/M8 无关。
        两条前提见 region_gate 模块 docstring:仅 TUN 全局代理下成立、区域通过 ≠ key 有效。
        国内 / 本地 / human / 占位槽自动 skipped 放行(不查 geo)。probe 可注入(离线测)。"""
        slot = self.config.slot(task_type)    # 未知 task_type → UnknownTaskType(归 M9 错误体系)
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
    """M9 对话入口。业务: from m9_gateway import call; call("distill", {...})"""
    return current_gateway().call(task_type, payload, bypass_cache=bypass_cache)


def embed(task_type: str, inputs: list) -> dict:
    """M9 嵌入入口。业务(M1e): from m9_gateway import embed; embed("embed_cloud", [text, ...])"""
    return current_gateway().embed(task_type, inputs)


def rerank(task_type: str, query: str, documents: list) -> dict:
    """M9 重排入口。业务(M3b): from m9_gateway import rerank; rerank("rerank", question, [text, ...])
       → {results:[{index, relevance_score}], ...}(按 index 对齐 documents)。"""
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
    """M9 页面 OCR 入口；业务模块不得绕过 Gateway 直连 provider。"""
    return current_gateway().ocr(
        task_type,
        image_bytes,
        mime_type=mime_type,
        page_number=page_number,
        source_sha256=source_sha256,
        data_ownership=data_ownership,
    )


def healthcheck(task_type: str) -> dict:
    """M9 嵌入安全阀健康探测入口。业务(M1e): healthcheck("embed_local") → {ready, message, ...}"""
    return current_gateway().healthcheck(task_type)


def region_preflight(task_type: str, *, probe=None) -> dict:
    """M9 境外 API 区域预检门入口。测境外 API 前:
        from m9_gateway import region_preflight
        rc = region_preflight("analysis")   # → {ok, gated, region, reason, message, ...}
    据 rc['gated'] 决定挡下 / 放行、rc['reason'] 判类;见 region_gate 模块 docstring 两条前提。"""
    return current_gateway().region_preflight(task_type, probe=probe)
