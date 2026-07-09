import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Seconds between keepalive comments on an idle SSE stream (defeats proxy idle timeouts).
HEARTBEAT_SECONDS = 20
# Per-subscriber queue bound. A backlog this deep only happens on a dead-but-not-yet-disconnected
# socket; overflow is dropped and the client reconciles via its reconnect resync.
_QUEUE_MAXSIZE = 100


class EventBroker:
    """In-memory pub/sub for real-time workspace events, keyed by workspace id.

    Single-process only: each subscriber gets its own asyncio.Queue and events are fanned out
    synchronously with `put_nowait`, so `publish` is safe to call without awaiting from an async
    request handler. Horizontal scaling (multiple uvicorn workers/instances) would require a
    shared backend (e.g. Redis) implementing this same subscribe/unsubscribe/publish surface.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._seq = 0

    def subscribe(self, workspace_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[workspace_id].add(queue)
        return queue

    def unsubscribe(self, workspace_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(workspace_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(workspace_id, None)

    def publish(self, workspace_id: str, event: dict[str, Any]) -> None:
        """Fan an event out to every subscriber of a workspace. Non-blocking and never raises."""
        self._seq += 1
        envelope = {**event, "seq": self._seq, "ts": datetime.now(UTC).isoformat()}
        for queue in list(self._subscribers.get(workspace_id, ())):
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                logger.warning(
                    "SSE queue full for workspace %s; dropping event %s (client resyncs on reconnect)",
                    workspace_id,
                    event.get("type"),
                )


# Module-level singleton shared across all requests in the process.
event_broker = EventBroker()
