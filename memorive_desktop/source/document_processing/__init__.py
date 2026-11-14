"""Memorive 文档预处理流水线。

传送带五段:DocumentIngest 导入 → DocumentConvert 转换/OCR → DocumentClean 清洗 → DocumentChunk 切块 → DocumentEmbed 嵌入。

分段入口:
    from document_processing import ingest, convert
    raw = ingest(pdf_path, data_ownership="self", doc_type="literature")
    result = convert(raw, paper_id="Smith2020", title="...", target="sandbox")

整线入口在 pipeline.py:run_document_processing 跑到切块为止,embed_from_chunks 对切好的块做嵌入。

调模型经 MODEL_GATEWAY(不直连 SDK)、每步留痕经 RUNTIME_LOG。
"""
from .document_ingest_ingest import ingest
from .document_convert_convert import convert
from .document_clean_clean import clean
from .document_chunk_chunk import chunk
from .document_embed_embed import embed
from .pipeline import (
    PipelineResult,
    embed_from_chunks,
    run_document_processing,
)

__all__ = [
    "ingest",
    "convert",
    "clean",
    "chunk",
    "embed",
    "run_document_processing",
    "embed_from_chunks",
    "PipelineResult",
]
