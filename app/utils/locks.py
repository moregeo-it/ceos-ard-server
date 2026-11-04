"""
Concurrency primitives for workspace operations.

LOCK ORDERING (single event loop, single process — see each lock's note):
    workspace lock (run_exclusive): OUTER
    fork_locks (per user): INNER — only ever taken while a workspace lock is held
    build_locks: independent — builds never take the workspace lock
    user_build_locks (per user): INNER — taken inside build_locks, never the other way round
    refresh_locks (per user): independent — taken in the auth dependency, before any handler runs

Never hold two workspace locks at once. The cross-process file lock sits strictly inside the
asyncio lock, so within the process it is uncontended; it exists to exclude the cron scripts
(scripts/cleanup_archived_workspaces.py), which cannot see asyncio locks.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from filelock import FileLock, Timeout

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
            # Outer finally so this runs even when the acquire itself is cancelled (a request
            # cancelled while queued): otherwise the increment above is never undone and
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


# One lock per workspace: every state-mutating workspace operation runs inside it via
# run_exclusive. This lock is what protects the git/index/worktree state — not the old
# "no awaits between check and mutation" discipline.
workspace_locks = KeyedLocks()

# One lock per user, guarding fork repair: GitHub allows one fork per account per upstream, so
# all of a user's workspaces share it and recreating it is a per-user operation.
fork_locks = KeyedLocks()

# One lock per build output prefix (workspace id + pfs selection): serializes preview builds
# that would clobber each other's output, without blocking file edits behind a 60s build.
build_locks = KeyedLocks()

# One lock per user around builds and what else runs in the build sandbox, e.g. rendering a file:
# a user waits for their own previous one instead of taking another of the BUILD_CONCURRENCY slots,
# so one user can't keep everyone else's builds waiting.
user_build_locks = KeyedLocks()

# One lock per user, serializing provider token refreshes: GitHub's refresh token is single-use,
# so concurrent requests wait for the first renewal instead of each spending it.
refresh_locks = KeyedLocks()


def workspace_lock_file(workspace_id: str):
    """Path of the cross-process lock file for a workspace, creating the lock directory."""
    lock_dir = settings.WORKSPACES_ROOT / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{workspace_id}.lock"


def acquire_workspace_file_lock(workspace_id: str, timeout: float) -> FileLock:
    """Take the per-workspace file lock, or raise TimeoutError; release it with `.release()`. For the cron scripts."""
    lock = FileLock(workspace_lock_file(workspace_id))
    lock.acquire(timeout=timeout)
    return lock


async def _acquire_workspace_file_lock(workspace_id: str) -> FileLock:
    # Polled on the event loop: filelock tracks locks per thread, so it must be released on the thread that took it
    lock = FileLock(workspace_lock_file(workspace_id))
    while True:
        try:
            lock.acquire(blocking=False)
            return lock
        except Timeout:
            await asyncio.sleep(0.1)


async def run_exclusive(workspace_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """
    Run one workspace-mutating transaction under the workspace lock and the cron scripts' flock.

    Shielded, so it finishes even if the request is cancelled: publish its events at the end of fn.
    """
    inner = asyncio.ensure_future(_run_locked(workspace_id, fn))
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        # The request was cancelled, not the transaction: it finishes in the background, and
        # its outcome is logged since nobody is left to observe the result.
        inner.add_done_callback(lambda task: _log_abandoned_outcome(workspace_id, task))
        raise


async def _run_locked(workspace_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    async with workspace_locks(workspace_id):
        lock = await _acquire_workspace_file_lock(workspace_id)
        try:
            return await fn()
        finally:
            lock.release()


def _log_abandoned_outcome(workspace_id: str, task: asyncio.Task) -> None:
    if task.cancelled():
        logger.warning(f"Abandoned transaction for workspace {workspace_id} was cancelled before finishing")
    elif exception := task.exception():
        logger.warning(f"Transaction for workspace {workspace_id}, abandoned by its cancelled request, failed: {exception}")
    else:
        logger.info(f"Transaction for workspace {workspace_id}, abandoned by its cancelled request, completed")
