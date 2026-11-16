"""The model router: task_type to slot (from the configuration), slot.provider to adapter, then the call.

Which provider handles a call comes from the configuration (slot.provider looked up in the adapter
registry), never from code such as if provider == 'deepseek'; changing the configuration changes the
provider.
"""
from __future__ import annotations

from memorive.models.gateway import providers
from memorive.models.gateway.errors import HumanSlot, SlotDisabled


class Router:
    def __init__(self, config):
        self.config = config

    def dispatch(self, task_type: str, payload: dict, *, capability: str = "chat") -> dict:
        slot = self.config.slot(task_type)          # 1) task_type -> slot
        if slot.provider == "human":                # a person's judgment: never a model
            raise HumanSlot(f"[{task_type}] 最终判断由人做,不经 MODEL_GATEWAY 调模型。")
        if not slot.enabled:                        # a disabled or placeholder slot
            raise SlotDisabled(
                f"[{task_type}] 槽位未启用(enabled=false);"
                f"如分析/校核占位槽,承 v6.3、暂不启用。"
            )
        adapter = providers.get_adapter(slot.provider)   # 2) provider -> adapter, from the registry
        if capability == "embed":                        # 3) embeddings
            return adapter.embed(slot, payload)
        if capability == "rerank":                       #    reranking
            return adapter.rerank(slot, payload)
        if capability == "ocr":                          #    page OCR; the gateway checks data ownership first
            return adapter.ocr(slot, payload)
        return adapter.invoke(slot, payload)             #    chat

    def preflight_model(self, task_type: str, *, timeout: int = 30) -> dict:
        """Run one provider-specific metadata probe through a common contract."""
        slot = self.config.slot(task_type)
        if slot.provider == "human":
            raise HumanSlot(f"[{task_type}] 最终判断由人做,无模型能力可探测。")
        if not slot.enabled:
            raise SlotDisabled(f"[{task_type}] 槽位未启用(enabled=false);不发起能力预检。")
        adapter = providers.get_adapter(slot.provider)
        return adapter.probe_model_capabilities(slot, timeout=timeout)
