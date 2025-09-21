# utils/logging_setup.py
import os, uuid, logging
from contextlib import contextmanager
from pythonjsonlogger import jsonlogger

_CORR_ID: str | None = None

def init_logging(app_name: str = "app", level: str = "INFO"):
    os.makedirs("logs", exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    fh = logging.FileHandler("logs/app.jsonl", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

def get_logger(name: str = "app") -> logging.Logger:
    return logging.getLogger(name)

def set_correlation_id(value: str | None = None) -> str:
    global _CORR_ID
    _CORR_ID = value or uuid.uuid4().hex[:8]
    return _CORR_ID

@contextmanager
def with_context(**extra):
    logger = get_logger("app")
    try:
        logger.info("ctx.enter", extra=extra)
        yield
    finally:
        logger.info("ctx.exit", extra=extra)

def log_exception(logger: logging.Logger, event: str):
    logger.exception(event)
