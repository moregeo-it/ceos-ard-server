"""
Concurrency primitives for workspace operations.

LOCK ORDERING (single event loop, single process — see each lock's note):
    workspace lock (run_exclusive): OUTER
    fork_locks (per user): INNER — only ever taken while a workspace lock is held
    build_locks: independent — builds never take the workspace lock

Never hold two workspace locks at once. The cross-process file lock sits strictly inside the
asyncio lock, so within the process it is uncontended; it exists to exclude the cron scripts
(scripts/cleanup_archived_workspaces.py), which cannot see asyncio locks.
"""

import asyncio
import fcntl
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

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


def workspace_lock_file(workspace_id: str):
    """Path of the cross-process lock file for a workspace, creating the lock directory."""
    lock_dir = settings.WORKSPACES_ROOT / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{workspace_id}.lock"


def acquire_workspace_file_lock(workspace_id: str, timeout: float | None = None) -> int:
    """
    Take the per-workspace flock, returning the file descriptor; closing it releases the lock.

    Blocking — call via asyncio.to_thread on the event loop. With a timeout, polls
    non-blocking and raises TimeoutError (used by the cron scripts to skip a busy workspace).
    """
    fd = os.open(workspace_lock_file(workspace_id), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Workspace {workspace_id} is locked by another process") from None
                    time.sleep(0.1)
    except BaseException:
        os.close(fd)
        raise
    return fd


async def run_exclusive(workspace_id: str, fn: Callable[[], Awaitable[T]]) -> T:
    """
    Run one workspace-mutating transaction under the per-workspace locks.

    workspace_locks serializes transactions in-process, across all their awaits. The flock
    excludes the cron cleanup script, a separate process. asyncio.shield lets the transaction
    finish when the request is cancelled (Starlette cancels handlers on client disconnect,
    and thread work like a push or rmtree cannot be cancelled) — without it, the lock would
    be released while a worker thread is still mutating the repository.
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
        fd = await asyncio.to_thread(acquire_workspace_file_lock, workspace_id)
        try:
            return await fn()
        finally:
            os.close(fd)


def _log_abandoned_outcome(workspace_id: str, task: asyncio.Task) -> None:
    if task.cancelled():
        logger.warning(f"Abandoned transaction for workspace {workspace_id} was cancelled before finishing")
    elif exception := task.exception():
        logger.warning(f"Transaction for workspace {workspace_id}, abandoned by a disconnected client, failed: {exception}")
    else:
        logger.info(f"Transaction for workspace {workspace_id}, abandoned by a disconnected client, completed")
