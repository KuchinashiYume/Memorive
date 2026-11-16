"""The registry of conversion engines, in the style of the gateway's provider registry.

config.ENGINE or convert(engine=...) chooses the engine; the calling code does not change.
Importing the package registers every engine (@register). Each engine loads marker, pymupdf or
markitdown only when it runs, so a missing one does not break the import; convert() reports it
when that engine is used.
"""
from __future__ import annotations

from memorive.documents.processing.engines.base import ConvertEngine
from memorive.documents.processing.errors import EngineNotAvailable

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


# importing registers each engine (order does not matter)
from memorive.documents.processing.engines import pymupdf_engine, markitdown_engine, passthrough_engine, marker_engine, model_gateway_ocr_engine  # noqa: E402,F401
