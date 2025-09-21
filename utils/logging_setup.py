# utils/logging_setup.py
import logging
from typing import Optional

_CORR_ID = "-"

class _CIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _CORR_ID
        record.app = "restoguide"
        return True

_logger: Optional[logging.Logger] = None

def init_logging(app_name: str = "restoguide", level: str = "INFO"):
    global _logger
    _logger = logging.getLogger(app_name)
    if not _logger.handlers:
        h = logging.StreamHandler()
        fmt = "%(asctime)s | %(levelname)-7s | %(app)s | %(correlation_id)s | %(name)s | %(message)s"
        h.setFormatter(logging.Formatter(fmt))
        h.addFilter(_CIDFilter())
        _logger.addHandler(h)
    _logger.setLevel(getattr(logging, level.upper(), logging.INFO))

def get_logger(name: str = "app") -> logging.Logger:
    if _logger is None:
        init_logging()
    return logging.getLogger(name)

def set_correlation_id(cid: str):
    global _CORR_ID
    _CORR_ID = cid

def log_exception(e: Exception):
    get_logger("app").exception(e)
