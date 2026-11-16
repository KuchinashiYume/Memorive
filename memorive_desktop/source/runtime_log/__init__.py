"""The runtime log: log.txt and its structured records, read by module, kept for the days chosen in Settings.

logger.py and records.py write, reader.py reads one module's lines, retention.py removes old days,
lock.py serializes writers. Most packages log through it. product_paths.py describes the roles of
product folders for artifact_registry, memorive_app and research_reports.
"""
from .logger import log, log_error
from .records import log_accounting, log_reason

__all__ = ["log", "log_error", "log_accounting", "log_reason"]

# Names the package offered before; restored with the code behind them.
from .reader import extract_module_logs
__all__ = [*__all__, 'extract_module_logs']
