"""Memorive MODEL-GATEWAY/MODEL_GATEWAY · MODEL_GATEWAY 网关错误类型骨架。

第 4 步在此扩展"四类报错分档"(key 失效 / 服务下线 / 分析模型不可用 / 网络瞬时)
及"哪个模块 + 哪个依赖 + 什么错 + 怎么处理"的文案与降级策略。本步只立基类与几个雏形。
"""
from __future__ import annotations


class GatewayError(Exception):
    """MODEL_GATEWAY 网关所有错误的基类。"""


class UnknownTaskType(GatewayError):
    """请求了配置里不存在的 task_type / 槽位。"""


class SlotDisabled(GatewayError):
    """槽位 enabled=false(如分析 / 校核占位槽,承 v6.3;暂不启用)。"""


class HumanSlot(GatewayError):
    """最终判断=人、不接模型(红线);不应经 MODEL_GATEWAY 发起模型调用。"""


class ProviderNotRegistered(GatewayError):
    """配置指定的 provider 没有对应 adapter 注册。→ 第 4 步②类(服务 / 后端不可用)雏形。"""


class ApiKeyMissing(GatewayError):
    """按 api_key_env 名在环境变量里取不到 key。→ 第 4 步①类(key 失效 / 缺失)雏形。"""

    def __init__(self, task_type: str, env_name: str):
        super().__init__(
            f"[{task_type}] 依赖的环境变量 {env_name} 未设置或为空;"
            f"请设置该 key 后重试(第 4 步将给出分类友好提示)。"
        )
        self.task_type = task_type
        self.env_name = env_name


class EmbedResultInvalid(GatewayError):
    """嵌入返回不合法(返回向量数量与输入数量不符等)。→ ServiceContracts 第 3 步:拒用、一条不写,
    防 chunk↔向量错位(错位比缺失更灾难:来源锚点全错且静默)。"""


class RerankResultInvalid(GatewayError):
    """重排返回不合法(返回分数条数与输入文档数不符等)。→ A(修跨篇混入):拒用、不记成功账,
    防 block↔score 错位(错位=把某块的相关性分安到另一块头上、静默污染检索排序/砍留)。"""


class OcrOwnershipBlocked(GatewayError):
    """OCR 云调用被数据归属闸门挡下。受托数据不得编码、上传或远程调用。"""


class OcrResultInvalid(GatewayError):
    """OCR 返回为空、页号/源哈希错位或缺少响应哈希；拒用且不记成功账。"""


class InvalidHealthcheckTarget(GatewayError):
    """healthcheck 用错了对象(仅 embed_local 安全阀可用)。→ ServiceContracts 第 3 步:受控挡下,
    归 MODEL_GATEWAY 错误体系(上层 catch GatewayError 即可统一处理),不漏成裸 ValueError。"""


class NonLocalEmbedEndpoint(GatewayError):
    """本地嵌入(ollama)的 endpoint 主机**非本地回环**(localhost/127.0.0.1/::1)。→ ServiceContracts 第 3 步加固:
    他人托付数据只允许发本地主机;endpoint 被误配/篡改成远程即挡下,**使「本地」成为代码可验证的不变量**,
    而非仅靠 models.yaml 一行文本。守「entrusted 绝不外发」红线的配置完整性防线。"""


class DependencyError(GatewayError):
    """MODEL_GATEWAY 依赖不可用的**受控错误**(非崩溃)。携带用户四类 error_class + 底层 root_class + 四段友好文案。
    调用方 / 交互界面应捕获它并展示 message,而非任其冒泡成 traceback。"""

    def __init__(self, error_class: str, root_class: str, task_type: str, message: str):
        super().__init__(message)
        self.error_class = error_class    # key_or_quota / service_unavailable / analysis_verify_unavailable / transient_exhausted
        self.root_class = root_class      # auth_or_quota / service_unavailable / transient_exhausted / slot_disabled
        self.task_type = task_type
        self.message = message
