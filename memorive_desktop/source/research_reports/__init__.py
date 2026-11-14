"""Deterministic, read-only RESEARCH_REPORTS research change log candidate."""

from .digest import GENERATOR_VERSION, build_digest

__all__ = ["GENERATOR_VERSION", "build_digest"]
