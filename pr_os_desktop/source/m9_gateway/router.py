"""Model Router:task_type → slot(读配置)→ provider → adapter → 分派。

关键:provider 分派由配置驱动(读 slot.provider 查注册表),
绝不 if provider == 'deepseek' 之类硬编码。改配置即换 provider。
"""
from __future__ import annotations

from . import providers
from .errors import HumanSlot, SlotDisabled


class Router:
    def __init__(self, config):
        self.config = config

    def dispatch(self, task_type: str, payload: dict, *, capability: str = "chat") -> dict:
        slot = self.config.slot(task_type)          # 1) task_type → slot(读配置)
        if slot.provider == "human":                # 红线:最终判断=人、不接模型(领域策略,非厂商分派)
            raise HumanSlot(f"[{task_type}] 最终判断由人做,不经 M9 调模型。")
        if not slot.enabled:                        # 占位/停用槽(如分析/校核)干净挡下、不崩
            raise SlotDisabled(
                f"[{task_type}] 槽位未启用(enabled=false);"
                f"如分析/校核占位槽,承 v6.3、暂不启用。"
            )
        adapter = providers.get_adapter(slot.provider)   # 2) provider → adapter(配置驱动、查注册表)
        if capability == "embed":                        # 3a) 嵌入能力(同一分派器只多一路;承 T3 第 3 步)
            return adapter.embed(slot, payload)
        if capability == "rerank":                       # 3a2) 重排能力(A·修跨篇混入;同一分派器再多一路)
            return adapter.rerank(slot, payload)
        if capability == "ocr":                          # 3a3) 页面级 OCR；所有权闸门在 Gateway 先行
            return adapter.ocr(slot, payload)
        return adapter.invoke(slot, payload)             # 3b) 对话:交给 adapter 发起真实调用

    def preflight_model(self, task_type: str, *, timeout: int = 30) -> dict:
        """Run one provider-specific metadata probe through a common contract."""
        slot = self.config.slot(task_type)
        if slot.provider == "human":
            raise HumanSlot(f"[{task_type}] 最终判断由人做,无模型能力可探测。")
        if not slot.enabled:
            raise SlotDisabled(f"[{task_type}] 槽位未启用(enabled=false);不发起能力预检。")
        adapter = providers.get_adapter(slot.provider)
        return adapter.probe_model_capabilities(slot, timeout=timeout)
