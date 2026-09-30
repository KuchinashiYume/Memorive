"""Memorive DOCUMENT-PROCESSING/DOCUMENT_PROCESSING · DOCUMENT_PROCESSING 预处理流水线。

传送带五段:DocumentIngest 导入 → DocumentConvert 转换/OCR → DocumentClean 清洗 → DocumentChunk 切块 → DocumentEmbed 嵌入。

对外分段入口(第 1 步已实现 DocumentIngest/DocumentConvert;整线 run_pipeline 待第 4 步串联):
    from document_processing import ingest, convert
    raw = ingest(pdf_path, data_ownership="self", doc_type="literature")
    result = convert(raw, paper_id="Smith2020", title="...", target="sandbox")

调模型经 MODEL_GATEWAY(不直连 SDK)、每步留痕经 RUNTIME_LOG。契约见
contracts/Core_最小可用闭环/10_模块契约/Memorive_DOCUMENT_PROCESSING_DOCUMENT_PROCESSING_模块契约与骨架设计.md。
"""
from .document_ingest_ingest import ingest
from .document_convert_convert import convert
from .document_clean_clean import clean
from .document_chunk_chunk import chunk
from .document_embed_embed import embed
from .pipeline import (PipelineResult, embed_from_chunks, re_embed_from_rawmd,
                       resume_after_note_review, run_document_processing,
                       run_pipeline)

__all__ = ["ingest", "convert", "clean", "chunk", "embed",
           "run_pipeline", "run_document_processing", "embed_from_chunks",
           "resume_after_note_review", "re_embed_from_rawmd", "PipelineResult"]
