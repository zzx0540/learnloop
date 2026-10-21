import asyncio
import time
import uuid
from collections import deque


class ChatRateLimiter:
    def __init__(self, *, limit: int = 6, window_seconds: float = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._active: set[uuid.UUID] = set()
        self._messages: dict[uuid.UUID, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: uuid.UUID) -> bool:
        now = time.monotonic()
        async with self._lock:
            timestamps = self._messages.setdefault(user_id, deque())
            while timestamps and now - timestamps[0] >= self.window_seconds:
                timestamps.popleft()
            if user_id in self._active or len(timestamps) >= self.limit:
                return False
            self._active.add(user_id)
            timestamps.append(now)
            return True

    async def release(self, user_id: uuid.UUID) -> None:
        async with self._lock:
            self._active.discard(user_id)

    async def reset(self) -> None:
        async with self._lock:
            self._active.clear()
            self._messages.clear()


CHAT_RATE_LIMITER = ChatRateLimiter()
