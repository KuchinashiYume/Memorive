"""PR-OS P01/T03/M01 · M1 流水线受控错误。均为受控异常(非崩溃),调用方捕获后展示 message;
每处抛错前已在 M1a/M1b 写 M11 detail 日志(自带 step+error 上下文)。"""
from __future__ import annotations


class M1Error(Exception):
    """M1 流水线通用受控错误(源文件缺失 / 归属未声明 / paper_id 碰撞等)。"""


class EngineNotAvailable(M1Error):
    """转换引擎未注册 / 未安装(如正式主力 marker 未装);不静默降级。"""


class OcrSafetyValve(M1Error):
    """安全阀①:输入需 OCR(图片 / 扫描件)但 m9_ocr 未就绪。
    绝不静默降级到 pytesseract / marker 内部 OCR / 任何直连模型 SDK。"""


class ScanSuspectError(M1Error):
    """安全阀②:数字版 PDF 经转换后文本量异常少,疑似扫描 / 图片型 PDF。
    拒绝静默产出空 [RawMD];应改走 OCR(经 M9)路径。"""


class TextIntegrityError(M1Error):
    """安全阀③:PDF 转换文本含灾难性 U+FFFD 损坏。
    拒绝把确定性不可处理文本继续送入清洗、切块、嵌入和下游蒸馏。"""


# ── M1e 嵌入受控错误(T3 第 3 步;承约束五:不裸 ValueError)──────────
class OwnershipMissingError(M1Error):
    """M1e:chunk 的 data_ownership 缺失 / 空 → 归属不明,**绝不默认上云**,挡下待人确认(承拍板①)。"""


class OwnershipRouteError(M1Error):
    """M1e:data_ownership 值非法(非 self/entrusted),或同篇 chunks 归属混合 → 无法分流,挡下。"""


class EmbedLocalNotReadyError(M1Error):
    """M1e 安全阀(红线):他人托付须本地嵌入,但本地未就绪 → 报错挡下、**绝不静默上云**
    (承 D0 / M1e 安全阀);携带 M9 给的归属保护友好文案。"""


class VectorModelConflictError(M1Error):
    """M1e:换嵌入模型 = 整库重嵌;目标 collection 已含不同 embedding_model / vector_type →
    挡下,不新旧混查、不混卡片字段向量(承约束「整库重嵌检查在写入前」)。"""


class VectorDuplicateError(M1Error):
    """M1e:chunk_id 已存在 collection → 只增不改,拒重复写(要重嵌请清库 / 新 collection / 走重嵌流程)。
    ⚠ Chroma add 遇重复 id 静默忽略,故须此显式预检(承 chromadb smoke 发现)。"""


class VectorWriteVerifyError(M1Error):
    """M1e 写后校验失败:col.add 后取回的数量 / metadata(run_id/model/vector_type)与本批不符 → 报错
    (承约束二:写后 post-write verification)。"""
