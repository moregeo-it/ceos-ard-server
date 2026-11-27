import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from app.utils.locks import workspace_generation


class WorkspaceCache:
    """
    Results computed from a workspace's files, until the workspace changes (see run_exclusive).

    Requests for a result that is being computed wait for it instead of computing it again.
    In memory, so for a single worker process, like the event broker.
    """

    def __init__(self, max_entries: int = 128):
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], tuple[int, asyncio.Future]] = OrderedDict()

    async def get(self, workspace_id: str, key: str, compute: Callable[[], Awaitable[Any]]) -> Any:
        generation = workspace_generation(workspace_id)
        entry = self._entries.get((workspace_id, key))
        if entry and entry[0] == generation:
            self._entries.move_to_end((workspace_id, key))
            # Shielded: a cancelled request doesn't cancel the computation others wait for
            return await asyncio.shield(entry[1])

        # Read before the computation starts: a change while it runs leaves the result outdated
        future = asyncio.ensure_future(compute())
        self._entries[(workspace_id, key)] = (generation, future)
        self._entries.move_to_end((workspace_id, key))
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        future.add_done_callback(lambda done: self._forget_failure(workspace_id, key, done))
        return await asyncio.shield(future)

    def _forget_failure(self, workspace_id: str, key: str, future: asyncio.Future) -> None:
        if future.cancelled() or future.exception() is not None:
            entry = self._entries.get((workspace_id, key))
            if entry and entry[1] is future:
                del self._entries[(workspace_id, key)]

    def clear(self) -> None:
        self._entries.clear()
