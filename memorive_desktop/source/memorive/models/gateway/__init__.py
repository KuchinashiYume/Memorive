"""Model transport: providers, routing, prompts, caches, cost, and the local model components.

Used outside the package: gateway.py (document processing, card extraction and review, research
analysis, retrieval, the workflow), which calls providers/ and router.py inside the package;
providers/anthropic.py and deepseek.py directly by memorive/desktop/settings; local_structured_chat.py (chat
with a local model) by memorive/desktop/settings and session refinement; prompts/ and prompt_cache.py;
research_prompt_cache.py; resource_limits.py; execution_core (runner, contracts, and codex_cli.py,
whose parser reads a Codex run's output); local_multimodal_ranker/ by retrieval.

Not reached from the app's own code: local_capability/ (python -m memorive.models.gateway.local_capability
shows status), local_embedding/, local_reranker/doctor.py, model_api_relay/, model_configuration/,
vision_qualification/, routing_advisory.py, reviewer_*.py, accounting/adapters and the other
command-line agent adapters in execution_core/adapters/. Tests call most of them; DEVELOPING.md lists
how the component APIs are entered.
"""
from memorive.models.gateway.gateway import (
    CANONICAL_EMBED_MODEL, ModelGateway, call, embed, healthcheck, ocr,
    region_preflight, rerank,
)
__all__ = [
    "call", "embed", "rerank", "ocr", "healthcheck", "region_preflight",
    "ModelGateway", "CANONICAL_EMBED_MODEL",
]
