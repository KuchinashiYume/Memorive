"""Deterministic, read-only RESEARCH_REPORTS research change log candidate."""

from .digest import GENERATOR_VERSION, build_digest
from .source import load_source_snapshot

__all__ = ["GENERATOR_VERSION", "build_digest", "load_source_snapshot"]
