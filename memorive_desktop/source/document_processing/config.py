"""Memorive DOCUMENT-PROCESSING/DOCUMENT_PROCESSING · DOCUMENT_PROCESSING 预处理流水线路径与引擎配置(单一配置源,不散落硬编码)。

承 ServiceContracts 第 0 步契约 §〇:路径一律经环境变量覆盖,默认值只在本文件出现一次。
引擎分两层(承拍板 1 精修):
  - PDF_PRIMARY = "marker"        正式主力(保留;未装→正式 target 报错、不静默降级)
  - PDF_SANDBOX_LIGHT="pymupdf4llm"  ⚠ 仅沙盒数字版 PDF 的临时轻量引擎、非主力
正式 target 用主力;sandbox target 用轻量;也可 convert(engine=...) 显式覆盖。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _resolve(default: str, *env_names: str) -> Path:
    """路径解析:优先取环境变量,缺省回落 default(默认值仅此一处)。"""
    for name in env_names:
        v = os.environ.get(name)
        if v and v.strip():
            return Path(v)
    return Path(default)


# ── 路径变量(默认值仅此一处;env 可覆盖,不许他处硬编码)──────────
VAULT_ROOT = _resolve(r"G:\Memorive", "MEMORIVE_VAULT_ROOT")            # 正式 Vault 根
OPS_ROOT = _resolve(r"G:\Memorive-运维", "MEMORIVE_OPS_ROOT")           # 运维区根(日志 / 备份)
SANDBOX_ROOT = _resolve(r"G:\Memorive-沙盒", "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT")  # 内测沙盒根(库外、可整体删)
# Windows 原生 HNSW 在非 ASCII persist path 下会漏写索引二进制;向量库单独落纯 ASCII 根。
CHROMA_ROOT = _resolve(r"G:\Memorive-ops\vector-store", "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT")


def root_for(target: str = "sandbox") -> Path:
    """target=sandbox→沙盒根;vault→正式 Vault 根。第 1 步内测走 sandbox。"""
    if target == "sandbox":
        return SANDBOX_ROOT
    if target == "vault":
        return VAULT_ROOT
    raise ValueError(f"未知 target: {target!r}(应为 'sandbox' | 'vault')")


def library_root(target: str = "sandbox") -> Path:
    """文献库根(单篇文件夹落于此)。"""
    return root_for(target) / "文献库"


# ── 引擎定位(两层;承拍板 1)──────────────────────────────────────
PDF_PRIMARY = "marker"              # 正式主力(未装 → 报错 + 提示装/改配置,绝不静默降级)
PDF_SANDBOX_LIGHT = "pymupdf4llm"   # ⚠ 轻量沙盒引擎、非主力:仅沙盒数字版 PDF 临时用


def engine_for(fmt: str, target: str = "sandbox") -> str:
    """按格式 + target 选引擎(配置驱动;可被 convert(engine=...) 覆盖)。"""
    if fmt == "pdf":
        return PDF_SANDBOX_LIGHT if target == "sandbox" else PDF_PRIMARY
    exact_engines = {
        "txt": "markitdown",
        "md": "passthrough",
        "csv": "markitdown",
        "html": "markitdown",
        "docx": "markitdown",
        "pptx": "markitdown",
        "xlsx": "markitdown",
        "doc": "markitdown",
        "ppt": "markitdown",
        "xls": "markitdown",
        "jpeg": "model_gateway_ocr",
        "png": "model_gateway_ocr",
        "tiff": "model_gateway_ocr",
        # Compatibility for already-materialized pre-Intake RawObject values. New
        # ingest never emits this generic key.
        "image": "model_gateway_ocr",
    }
    try:
        return exact_engines[fmt]
    except KeyError as exc:
        raise ValueError(
            f"未知格式: {fmt!r};只允许 DOCUMENT-FORMATS 冻结的 14 种格式"
        ) from exc


# ── OCR 经 MODEL_GATEWAY 就绪标志─────────────────────────────────────────
def _bool_setting(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false，实际为 {raw!r}")


# 默认关闭；仅隔离验收或后续正式批准环境显式开启，避免代码存在即等于生产启用。
OCR_VIA_MODEL_GATEWAY_READY = _bool_setting("MEMORIVE_DOCUMENT_PROCESSING_OCR_VIA_MODEL_GATEWAY_READY", False)
OCR_PAGE_RENDER_DPI = int(os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_OCR_PAGE_RENDER_DPI") or 200)
if not 72 <= OCR_PAGE_RENDER_DPI <= 400:
    raise ValueError("MEMORIVE_DOCUMENT_PROCESSING_OCR_PAGE_RENDER_DPI 必须在 72..400")

# ── 疑似扫描件阈值(安全阀②;可配置,env 可覆盖)──────────────────
# 数字版引擎提取文本每页低于此字符数 → 疑扫描/图片型 PDF,报错不静默产出空 [RawMD]。
# 默认 50/页(真实数字版每页远超此数;扫描页 ~0 字);env MEMORIVE_DOCUMENT_PROCESSING_SCAN_MIN_CHARS_PER_PAGE 可调。
# 误挡真短文档(补充材料/一页摘要)时,由 convert(allow_short=True) 人工确认放行(见 document_convert_convert)。
SCAN_SUSPECT_MIN_CHARS_PER_PAGE = int(os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_SCAN_MIN_CHARS_PER_PAGE") or 50)


def _ratio_setting(name: str, default: float) -> float:
    """读取 0～1 安全阈值；非法配置 fail-fast，避免静默关闭安全阀。"""
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw and raw.strip() else default
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 0～1 之间的数字，实际为 {raw!r}") from exc
    if not 0 < value <= 1:
        raise ValueError(f"{name} 必须满足 0 < value <= 1，实际为 {value!r}")
    return value


# ── PDF 文本完整性阈值(安全阀③;只识别 U+FFFD 确定性转换损坏)───────
# 10 篇 Final 冻结样本实测：8 篇可读样本全文替换字符 0～0.0521%、坏页 0；
# VanDerZee2001 全文 91.4050%、坏页 8/8；Guo2011 坏页 44/200。
# 同一 replacement 阈值用于全文与单页；坏页达到文档页数阈值后整篇 fail-closed。
TEXT_REPLACEMENT_FAIL_RATIO = _ratio_setting(
    "MEMORIVE_DOCUMENT_PROCESSING_TEXT_REPLACEMENT_FAIL_RATIO", 0.20)
CORRUPT_PAGE_FRACTION_FAIL_RATIO = _ratio_setting(
    "MEMORIVE_DOCUMENT_PROCESSING_CORRUPT_PAGE_FRACTION_FAIL_RATIO", 0.20)

# ── Chroma collection 名(承第 0 步沙盒独立 collection + 第 3 步拍板②)──
COLLECTION = {"sandbox": "memorive_sandbox", "vault": "memorive_main"}


def chroma_dir(target: str = "sandbox") -> Path:
    """Chroma 物理目录(纯 ASCII 库外运维区,承 D2 隔离 + 沙盒/正式双隔离)。
    sandbox → {CHROMA_ROOT}/chroma-sandbox-v2/;vault → {CHROMA_ROOT}/chroma-vault/。
    根可由 MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT 覆盖,两类目录仍可由既有 env 直接覆盖(测试用)。
    与 collection 双隔离:沙盒独目录 + memorive_sandbox,正式独目录 + memorive_main。"""
    if target == "sandbox":
        v = os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_CHROMA_DIR")
        return Path(v) if v and v.strip() else CHROMA_ROOT / "chroma-sandbox-v2"
    if target == "vault":
        v = os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_CHROMA_DIR")
        return Path(v) if v and v.strip() else CHROMA_ROOT / "chroma-vault"
    raise ValueError(f"未知 target: {target!r}(应为 'sandbox' | 'vault')")


def now_iso() -> str:
    """本地带时区时间戳,秒级(与 RUNTIME_LOG 同风格)。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")
