# utils/logging_setup.py
from __future__ import annotations
import logging, os, sys, json, time, uuid, contextvars
from logging.handlers import TimedRotatingFileHandler
from pythonjsonlogger import jsonlogger

# ===== سياق مشترك يُحقن بكل Log =====
_correlation_id = contextvars.ContextVar("correlation_id", default="-")
_ctx_dict = contextvars.ContextVar("ctx_dict", default={})
_app_name = contextvars.ContextVar("app_name", default="app")

def set_correlation_id(cid: str | None = None) -> str:
    cid = cid or uuid.uuid4().hex[:8]
    _correlation_id.set(cid)
    return cid

def update_context(**kwargs):
    base = dict(_ctx_dict.get().copy())
    base.update({k: v for k, v in kwargs.items() if v is not None})
    _ctx_dict.set(base)

class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # حقن الحقول لكل Log
        record.correlation_id = _correlation_id.get()
        ctx = _ctx_dict.get()
        for k, v in (ctx or {}).items():
            setattr(record, k, v)
        record.app = _app_name.get()
        return True

class JsonFormatter(jsonlogger.JsonFormatter):
    def process_log_record(self, log_record):
        # إزالة None/حقول طويلة جداً
        for k, v in list(log_record.items()):
            if v is None:
                del log_record[k]
            elif isinstance(v, str) and len(v) > 8000:
                log_record[k] = v[:8000] + "…"
        # طابع زمني موحّد
        log_record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return super().process_log_record(log_record)

def init_logging(app_name: str = "app", level: str = "INFO",
                 log_dir: str = "logs", json_filename: str = "app.jsonl"):
    """تهيئة logging: كونسول Rich + ملف JSONL مع تدوير يومي."""
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass

    _app_name.set(app_name)

    # مُستوى
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl)  # لضبط الجذر

    # فلتر السياق
    ctx_filter = ContextFilter()

    # ===== Console (Rich-like بدون اعتماد خارجي) =====
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(lvl)
    console.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(app)s | %(correlation_id)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S"
    ))
    console.addFilter(ctx_filter)

    # ===== File JSON =====
    json_path = os.path.join(log_dir, json_filename)
    fileh = TimedRotatingFileHandler(json_path, when="midnight", backupCount=7, encoding="utf-8")
    fileh.setLevel(lvl)
    fields = ["ts","levelname","app","name","message","correlation_id"]
    # نترك أي حقول سياقية إضافية تمر تلقائيًا
    fileh.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(app)s %(name)s %(message)s"))
    fileh.addFilter(ctx_filter)

    root = logging.getLogger()
    # إزالة هاندلرز افتراضية مكررة
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(console)
    root.addHandler(fileh)
    root.setLevel(lvl)

def set_level(level: str = "INFO"):
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(lvl)
    for h in logging.getLogger().handlers:
        h.setLevel(lvl)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

# ===== أدوات مساعدة =====
from contextlib import contextmanager
@contextmanager
def with_context(**kwargs):
    """Context manager لحقن سياق مؤقت داخل البلوك."""
    old = _ctx_dict.get().copy()
    update_context(**kwargs)
    try:
        yield
    finally:
        _ctx_dict.set(old)

def log_exception(logger: logging.Logger, msg: str, **kwargs):
    update_context(**kwargs)
    logger.exception(msg)

@contextmanager
def log_timing(logger: logging.Logger, label: str, **kwargs):
    update_context(**kwargs)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = (time.perf_counter() - t0) * 1000.0
        logger.info("timing", extra={"label": label, "ms": round(dt, 2)})

def tail(path: str, n: int = 300) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "".join(lines[-n:])
    except Exception as e:
        return f"# لا يمكن قراءة السجل: {e}"
