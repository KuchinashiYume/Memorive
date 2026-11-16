"""Research reports: daily, weekly and monthly digests of what changed in the library and in research.

The research runtime renders them on schedule (digest.py, renderer.py, api_briefing.py).
python -m research_reports (cli.py) builds a report by hand from a source folder.
"""

from .digest import GENERATOR_VERSION, build_digest

__all__ = ["GENERATOR_VERSION", "build_digest"]

# Names the package offered before; restored with the code behind them.
from .source import load_source_snapshot
__all__ = [*__all__, 'load_source_snapshot']
