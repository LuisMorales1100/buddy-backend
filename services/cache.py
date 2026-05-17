import time
from typing import Optional, Any

class MemoryCache:
    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, tuple[Any, float]] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        data, expires = self._store[key]
        if time.time() > expires:
            del self._store[key]
            return None
        return data

    def set(self, key: str, data: Any, ttl: Optional[int] = None):
        expires = time.time() + (ttl or self.default_ttl)
        self._store[key] = (data, expires)

    def delete(self, key: str):
        self._store.pop(key, None)

cache = MemoryCache()