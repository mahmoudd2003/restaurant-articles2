# utils/llm_cache.py
import json, hashlib
from datetime import timedelta
from typing import Any, Dict, Optional

try:
    from diskcache import Cache
except Exception:
    Cache = None

DEFAULT_CACHE_DIR = "data/llm_cache"

class LLMCacher:
    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, ttl_hours: int = 24, enabled: bool = True):
        self.enabled = enabled and (Cache is not None)
        self.ttl = max(1, int(ttl_hours)) * 3600
        self.cache = Cache(cache_dir) if self.enabled else None

    def configure(self, enabled: bool = None, ttl_hours: int = None):
        if enabled is not None:
            self.enabled = enabled and (self.cache is not None or Cache is not None)
            if self.enabled and self.cache is None and Cache is not None:
                self.cache = Cache(DEFAULT_CACHE_DIR)
        if ttl_hours is not None:
            self.ttl = max(1, int(ttl_hours)) * 3600

    def make_key(self, *, model: str, temperature: float, max_tokens: int, messages: list, extra: Optional[Dict[str, Any]] = None) -> str:
        # طبع JSON ثابت لضمان مفاتيح متطابقة لنفس الطلب
        payload = {
            "model": model,
            "temperature": round(float(temperature), 2),
            "max_tokens": int(max_tokens),
            "messages": messages,
            "extra": extra or {},
        }
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        if not self.enabled or self.cache is None: return None
        try:
            return self.cache.get(key, default=None)
        except Exception:
            return None

    def set(self, key: str, value: str):
        if not self.enabled or self.cache is None: return
        try:
            self.cache.set(key, value, expire=self.ttl)
        except Exception:
            pass

    def clear(self) -> bool:
        if not self.enabled or self.cache is None: return False
        try:
            self.cache.clear()
            return True
        except Exception:
            return False
