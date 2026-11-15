"""Deterministic, read-only RESEARCH_REPORTS research change log candidate."""

from .digest import GENERATOR_VERSION, build_digest

__all__ = ["GENERATOR_VERSION", "build_digest"]

# Names the package offered before; restored with the code behind them.
from .source import load_source_snapshot
__all__ = [*__all__, 'load_source_snapshot']
