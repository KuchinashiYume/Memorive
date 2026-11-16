"""Model transport: providers, routing, prompts, caches, cost, and the local model components.

Used by the app: providers/ (OpenAI-compatible, Anthropic, DeepSeek, SiliconFlow, Ollama) through
gateway.py and router.py; prompts/ and prompts.py; local_structured_chat.py (chat with a local
model); research_prompt_cache.py and the cache modules; resource_limits.py (output and time limits);
execution_core/adapters/codex_cli.py, whose parser reads a Codex run's output.

Component APIs that a caller configures and starts: local_capability/ (python -m
model_gateway.local_capability shows status), local_embedding/, local_reranker/,
local_multimodal_ranker/, model_api_relay/, vision_qualification/, and the other command-line
agent adapters in execution_core/adapters/. DEVELOPING.md lists how each is entered.
"""
from .gateway import (
    CANONICAL_EMBED_MODEL, ModelGateway, call, embed, healthcheck, ocr,
    region_preflight, rerank,
)
__all__ = [
    "call", "embed", "rerank", "ocr", "healthcheck", "region_preflight",
    "ModelGateway", "CANONICAL_EMBED_MODEL",
]
