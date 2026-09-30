"""Memorive MODEL-GATEWAY/MODEL_GATEWAY · MODEL_GATEWAY Provider adapter 契约。

每个 provider 一个 adapter,自己负责该家的 auth / 请求构造 / 响应解析,
并把响应归一化成统一形状返回。
"""
from __future__ import annotations


class ProviderAdapter:
    def probe_model_capabilities(self, slot, *, timeout: int = 30) -> dict:
        """Make a metadata-only live request and return normalized capabilities.

        The request must not send user content.  Provider adapters decide which
        metadata endpoint and, if required, which frozen capability registry to
        use.  This makes the startup contract provider-neutral at Router level.
        """
        raise NotImplementedError

    def invoke(self, slot, payload: dict) -> dict:
        """[chat] 按 slot + payload 发起一次对话调用,
        返回归一化 dict: {task_type, provider, model, text, usage, raw}。"""
        raise NotImplementedError

    def embed(self, slot, payload: dict) -> dict:
        """[embed] 按 slot + payload(含 'input')发起一次嵌入调用,
        返回归一化 dict: {task_type, provider, model, embeddings:[[...]], usage, raw}。
        raw 仅供函数内/测试用,绝不进日志(承 ServiceContracts 第 3 步安全约束)。"""
        raise NotImplementedError

    def rerank(self, slot, payload: dict) -> dict:
        """[rerank] 按 slot + payload(含 'query' + 'documents')发起一次重排调用(A·修跨篇混入),
        返回归一化 dict: {task_type, provider, model, results:[{index, relevance_score}], usage, raw}。
        results 须按 index 升序、对齐 documents(承 embed 同款防 block↔score 错位口径);
        raw 仅供函数内/测试用,绝不进日志。"""
        raise NotImplementedError

    def ocr(self, slot, payload: dict) -> dict:
        """[ocr] 单页图片转 Markdown。

        payload 只在所有权闸门通过后进入 adapter，含 image_bytes、mime_type、
        page_number、source_sha256 与版本化 prompt；返回必须回显页号和源哈希，
        并提供响应 SHA-256 供 DOCUMENT_PROCESSING 双路校核留痕。
        """
        raise NotImplementedError

    def healthcheck(self, slot) -> dict:
        """[embed 安全阀专用] 纯能力探测(如 GET /api/tags),**不发任何待嵌文本**;
        返回结构化状态 dict(至少含 'ready': bool)。挡下动作由调用方(DocumentEmbed)据 ready 决定。"""
        raise NotImplementedError
