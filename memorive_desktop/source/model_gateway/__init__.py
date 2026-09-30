"""Memorive MODEL-GATEWAY/MODEL_GATEWAY · MODEL_GATEWAY 模型网关。

对外只暴露 call() / ModelGateway。

业务模块用法:
    from model_gateway import call
    resp = call("distill", {"messages": [{"role": "user", "content": "..."}]})

业务侧全程不 import 任何模型厂商 SDK;换模型只改 models.yaml。

嵌入(ServiceContracts 第 3 步):
    from model_gateway import embed, healthcheck
    healthcheck("embed_local")            # 安全阀预检:纯探测本地就绪(仅 embed_local)
    embed("embed_cloud", ["文本1", ...])  # 自有→云;他人托付→ embed("embed_local", ...)

重排(A·修跨篇混入):
    from model_gateway import rerank
    rerank("rerank", question, ["块1", ...])  # cross-encoder 精排,回 results:[{index, relevance_score}]

境外 API 区域预检门(测前安全阀,承 v6.4 待并入之二):
    from model_gateway import region_preflight
    region_preflight("analysis")          # 测境外 API 前查出口区域:大陆挡下 / 非陆放行 / 查不到交人

COST-ROUTING 只读路由建议（不调用 provider、不写生产 route）:
    from model_gateway import make_routing_input_window_snapshot, preview_route_advice
    snapshot = make_routing_input_window_snapshot(...)
    advice = preview_route_advice(snapshot, role_family="analysis_primary")
"""
from .gateway import (
    CANONICAL_EMBED_MODEL, ModelGateway, call, embed, healthcheck, ocr,
    region_preflight, rerank,
)
__all__ = [
    "call", "embed", "rerank", "ocr", "healthcheck", "region_preflight",
    "ModelGateway", "CANONICAL_EMBED_MODEL",
]
