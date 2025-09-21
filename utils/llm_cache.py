# utils/llm_cache.py
from typing import Optional
from diskcache import Cache

class LLMCacher:
    def __init__(self, path: str = "data/llm_cache", ttl_hours: int = 24, enabled: bool = True):
        self.cache = Cache(path)
        self.enabled = enabled
        self.ttl = int(ttl_hours * 3600)

    def configure(self, *, enabled: Optional[bool] = None, ttl_hours: Optional[int] = None):
        if enabled is not None:
            self.enabled = enabled
        if ttl_hours is not None:
            self.ttl = int(ttl_hours * 3600)

    def get(self, key: str):
        if not self.enabled:
            return None
        return self.cache.get(key)

    def set(self, key: str, value: str):
        if not self.enabled:
            return
        self.cache.set(key, value, expire=self.ttl)

    def clear(self) -> bool:
        try:
            self.cache.clear()
            return True
        except Exception:
            return False
