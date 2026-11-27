import asyncio

import pytest

from app.utils import locks
from app.utils.locks import run_exclusive
from app.utils.workspace_cache import WorkspaceCache


def counter():
    calls = []

    async def compute():
        calls.append(None)
        await asyncio.sleep(0.01)
        return len(calls)

    return calls, compute


def test_concurrent_requests_share_one_computation():
    cache = WorkspaceCache()
    calls, compute = counter()

    async def main():
        return await asyncio.gather(*(cache.get("ws", "a", compute) for _ in range(3)))

    assert asyncio.run(main()) == [1, 1, 1]
    assert len(calls) == 1


def test_results_are_recomputed_after_a_change_of_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(locks.settings, "WORKSPACES_ROOT", tmp_path)
    cache = WorkspaceCache()
    calls, compute = counter()

    async def change():
        pass

    async def main():
        first = await cache.get("ws", "a", compute)
        again = await cache.get("ws", "a", compute)
        other = await cache.get("other", "a", compute)
        await run_exclusive("ws", change)
        changed = await cache.get("ws", "a", compute)
        return first, again, other, changed

    assert asyncio.run(main()) == (1, 1, 2, 3)


def test_a_change_during_the_computation_outdates_its_result(tmp_path, monkeypatch):
    monkeypatch.setattr(locks.settings, "WORKSPACES_ROOT", tmp_path)
    cache = WorkspaceCache()
    calls, compute = counter()

    async def change():
        pass

    async def main():
        running = asyncio.ensure_future(cache.get("ws", "a", compute))
        await asyncio.sleep(0)
        await run_exclusive("ws", change)
        first = await running
        return first, await cache.get("ws", "a", compute)

    assert asyncio.run(main()) == (1, 2)


def test_failures_are_not_kept():
    cache = WorkspaceCache()
    calls = []

    async def fail():
        calls.append(None)
        raise ValueError("failed")

    async def main():
        for _ in range(2):
            with pytest.raises(ValueError):
                await cache.get("ws", "a", fail)

    asyncio.run(main())
    assert len(calls) == 2


def test_the_least_recently_used_results_are_dropped():
    cache = WorkspaceCache(max_entries=2)
    calls, compute = counter()

    async def main():
        for key in ("a", "b", "a", "c", "a", "b"):
            await cache.get("ws", key, compute)

    asyncio.run(main())
    # a, b, c computed, a kept as recently used, b dropped for c and computed again
    assert len(calls) == 4
