"""Memorive DOCUMENT-PROCESSING/DOCUMENT_PROCESSING · DOCUMENT_PROCESSING 转换引擎注册表。

采用 model_gateway/providers 风格的注册表分派。

改 config.ENGINE 定位或 convert(engine=...) 即换引擎,业务代码不动(承 D3 配置驱动)。
import 本包即触发各引擎 @register(引擎内部对 marker/pymupdf/markitdown 均为懒加载,
未装也不影响 import——真用到未装引擎时才在 convert() 报友好错)。
"""
from __future__ import annotations

from .base import ConvertEngine
from ..errors import EngineNotAvailable

_ENGINES: dict[str, type[ConvertEngine]] = {}


def register(cls: type[ConvertEngine]) -> type[ConvertEngine]:
    _ENGINES[cls.name] = cls
    return cls


def get_engine(name: str) -> ConvertEngine:
    if name not in _ENGINES:
        raise EngineNotAvailable(
            f"转换引擎 '{name}' 未注册;改 config 引擎定位或先安装/登记该引擎。")
    return _ENGINES[name]()


def registered() -> list[str]:
    return sorted(_ENGINES)


# 触发注册(顺序无关)
from . import pymupdf_engine, markitdown_engine, passthrough_engine, marker_engine, model_gateway_ocr_engine  # noqa: E402,F401
