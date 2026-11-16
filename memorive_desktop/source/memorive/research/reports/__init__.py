"""Research reports: daily, weekly and monthly digests of what changed in the library and in research.

The research runtime renders them on schedule (digest.py, renderer.py, policy.py, api_briefing.py).
The runtime's side is here too: periodic.py and schedule.py (when a report is due), identity.py,
models.py (the model profiles reports and briefings may use), digest_builder.py and
library_projection.py (reports and discovery results in the library).
python -m memorive.research.reports (cli.py) builds a report by hand from a source folder; the test console
bridge also calls cli.py.
"""

from memorive.research.reports.digest import GENERATOR_VERSION, build_digest

__all__ = ["GENERATOR_VERSION", "build_digest"]

# Names the package offered before; restored with the code behind them.
from memorive.research.reports.source import load_source_snapshot
__all__ = [*__all__, 'load_source_snapshot']
