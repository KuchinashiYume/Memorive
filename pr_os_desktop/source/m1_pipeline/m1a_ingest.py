"""M1a 导入 —— 传送带第一节。

接收一个原始文件(PDF/图片/笔记/已有 MD),记录来源、导入时间、**数据归属标记**、
文档类型、原始格式,输出 RawObject + 元数据。归属标记是红线(承第 0 步硬约束①):
未声明不放行;它经 RawObject 一路传给 M1b→M1c→M1d→M1e。
守边界:M1a 只登记、不转换。
"""
from __future__ import annotations

from pathlib import Path

from m11_log import log, log_error

from .config import now_iso
from .errors import M1Error
from .types import IngestMeta, RawObject

_FORMAT_BY_EXTENSION = {
    "pdf": "pdf",
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "csv": "csv",
    "html": "html",
    "htm": "html",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "xlsx",
    "doc": "doc",
    "ppt": "ppt",
    "xls": "xls",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "tif": "tiff",
    "tiff": "tiff",
}


def _infer_format(p: Path) -> str:
    ext = p.suffix.lower().lstrip(".")
    try:
        return _FORMAT_BY_EXTENSION[ext]
    except KeyError as exc:
        raise M1Error(
            f"不支持的文档格式: {p.suffix or '<none>'};"
            "只允许 P06/T05 冻结的 14 种格式"
        ) from exc


def ingest(source_path, *, data_ownership: str, doc_type: str = "literature") -> RawObject:
    """导入一个原始文件。data_ownership 必填('self'|'entrusted')。"""
    p = Path(source_path)

    # 补充要求①:源文件必须存在,否则写 M11 detail + 抛受控错。
    if not p.exists() or not p.is_file():
        log_error("M1a", "源文件不存在", context={
            "step": "ingest", "error": f"源文件不存在或非文件: {p}", "source": str(p)})
        raise M1Error(f"源文件不存在或非文件: {p}")

    # 归属红线:未声明 / 非法值不放行。
    if data_ownership not in ("self", "entrusted"):
        log_error("M1a", "数据归属未声明", context={
            "step": "ingest", "error": f"data_ownership 非法: {data_ownership!r}",
            "source": str(p)})
        raise M1Error("data_ownership 必填,只能是 'self'(自有)或 'entrusted'(他人托付)")

    fmt = _infer_format(p)
    meta = IngestMeta(source=str(p), imported_at=now_iso(),
                      data_ownership=data_ownership, doc_type=doc_type, original_format=fmt)
    log("M1a", "导入完成", data={
        "source": meta.source, "data_ownership": meta.data_ownership,
        "doc_type": meta.doc_type, "original_format": meta.original_format})
    return RawObject(path=p, meta=meta)
