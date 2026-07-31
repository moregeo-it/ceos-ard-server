import asyncio
from contextlib import asynccontextmanager


class KeyedLocks:
    """
    Async locks keyed by an arbitrary id, evicted once idle.

    Single event loop only — which is also all these locks are worth: a multi-process
    deployment needs a filesystem or database lock instead.
    """

    def __init__(self):
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def __call__(self, key: str):
        lock, users = self._locks.get(key, (None, 0))
        if lock is None:
            lock = asyncio.Lock()

        # Count the user before awaiting, so a concurrent release cannot evict the entry
        # while this caller is still queued for it.
        self._locks[key] = (lock, users + 1)

        try:
            await lock.acquire()
            try:
                yield
            finally:
                lock.release()
        finally:
            # Outer finally so this runs even when the acquire itself is cancelled (a client
            # disconnecting while queued): otherwise the increment above is never undone and
            # the entry can never be evicted. Indexing without a guard is safe — this caller
            # is still counted, so nobody else can have evicted the entry.
            _, users = self._locks[key]
            if users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)

    def held(self, key: str) -> bool:
        """Whether the lock for this key is currently held. For assertions and tests."""
        lock, _ = self._locks.get(key, (None, 0))
        return lock is not None and lock.locked()
