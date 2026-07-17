import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Keepalive ping interval (seconds) for idle realtime connections; defeats proxy idle timeouts.
HEARTBEAT_SECONDS = 20
# Per-subscriber queue bound; only reached by a dead-but-not-yet-disconnected socket.
_QUEUE_MAXSIZE = 100

# Enqueued when a subscriber's queue overflows: tells the consumer to close the stream so the
# client reconnects and resyncs. Dropping events silently is unsafe - a dropped share.revoked /
# workspace.deleted would leave an unauthorized stream open. `None` never collides with a real
# (dict) envelope.
FORCE_RESYNC = None


class EventBroker:
    """In-memory pub/sub for real-time workspace events, keyed by workspace id.

    Transport-agnostic: the realtime WebSocket gateway (app/api/collab.py) drains each subscriber's
    queue and sends it over the socket. Single-process only: each subscriber gets its own
    asyncio.Queue, fanned out synchronously via `put_nowait` (so `publish` needs no await).
    Horizontal scaling would require a shared backend (e.g. Redis) behind this same
    subscribe/unsubscribe/publish surface.
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
                self._force_resync(workspace_id, queue, event.get("type"))

    def _force_resync(self, workspace_id: str, queue: asyncio.Queue, dropped_event_type: str | None) -> None:
        """Drain the overflowed subscriber's backlog and enqueue FORCE_RESYNC so it reconnects.

        Draining first guarantees the sentinel fits; the discarded events are superseded by the
        client's post-reconnect resync.
        """
        logger.warning(
            "Realtime queue full for workspace %s; forcing subscriber to disconnect and resync (dropped %s)",
            workspace_id,
            dropped_event_type,
        )
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(FORCE_RESYNC)
        except asyncio.QueueFull:
            logger.error("Failed to enqueue forced-resync sentinel for workspace %s", workspace_id)


# Module-level singleton shared across all requests in the process.
event_broker = EventBroker()
