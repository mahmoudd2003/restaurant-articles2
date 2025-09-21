import logging, sys, uuid
from contextlib import contextmanager

_corr = {"id": "-"}

def init_logging(app_name="app", level="INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger(app_name).info("logging initialized")

def set_correlation_id(cid: str):
    _corr["id"] = cid

def get_logger(name: str):
    return logging.getLogger(name)

def with_context(msg: str) -> str:
    return f"{_corr.get('id','-')} | {msg}"

def log_exception(logger, e: Exception):
    logger.error(with_context(f"exception: {e}"))

@contextmanager
def context(logger, label):
    logger.info(with_context(f"{label}.start"))
    try:
        yield
        logger.info(with_context(f"{label}.done"))
    except Exception as e:
        logger.exception(with_context(f"{label}.error: {e}"))
        raise
