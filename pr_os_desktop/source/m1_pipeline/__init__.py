"""PR-OS P01/T03/M01 · M1 预处理流水线。

传送带五段:M1a 导入 → M1b 转换/OCR → M1c 清洗 → M1d 切块 → M1e 嵌入。

对外分段入口(第 1 步已实现 M1a/M1b;整线 run_pipeline 待第 4 步串联):
    from m1_pipeline import ingest, convert
    raw = ingest(pdf_path, data_ownership="self", doc_type="literature")
    result = convert(raw, paper_id="Smith2020", title="...", target="sandbox")

调模型经 M9(不直连 SDK)、每步留痕经 M11。契约见
项目文档/Phase01_最小可用闭环/10_模块契约/PR-OS_P01_T03_M01_模块契约与骨架设计.md。
"""
from .m1a_ingest import ingest
from .m1b_convert import convert
from .m1c_clean import clean
from .m1d_chunk import chunk
from .m1e_embed import embed
from .pipeline import (PipelineResult, embed_from_chunks, re_embed_from_rawmd,
                       resume_after_note_review, run_document_processing,
                       run_pipeline)

__all__ = ["ingest", "convert", "clean", "chunk", "embed",
           "run_pipeline", "run_document_processing", "embed_from_chunks",
           "resume_after_note_review", "re_embed_from_rawmd", "PipelineResult"]
