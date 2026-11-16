"""The runtime log: log.txt and its structured records, read by module, kept for the days chosen in Settings.

logger.py and records.py write, reader.py reads one module's lines, retention.py removes old days,
lock.py serializes writers, config.py says where log.txt lies. Most packages log through it.
"""
from memorive.platform.logs.logger import log, log_error
from memorive.platform.logs.records import log_accounting, log_reason

__all__ = ["log", "log_error", "log_accounting", "log_reason"]

# Names the package offered before; restored with the code behind them.
from memorive.platform.logs.reader import extract_module_logs
__all__ = [*__all__, 'extract_module_logs']
