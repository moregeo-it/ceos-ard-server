import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.schemas.events import EventType, build_event

logger = logging.getLogger(__name__)

# Keepalive ping interval (seconds) for idle realtime connections; defeats proxy idle timeouts.
HEARTBEAT_SECONDS = 20
# Per-subscriber queue bound; only reached by a dead-but-not-yet-disconnected socket.
_QUEUE_MAXSIZE = 100

# Application close codes (4000-4999) of the realtime socket, mirrored in openapi.yaml and realtime.js.
WS_CLOSE_SESSION_EXPIRED = 4001  # JWT invalid/expired or logged out: re-login, then reconnect
WS_CLOSE_ACCESS_REVOKED = 4003  # no access (anymore): stop
WS_CLOSE_RESYNC = 4009  # events dropped: reconnect and resync


@dataclass(frozen=True)
class CloseSignal:
    """Queue item that closes the socket with a code instead of delivering an event."""

    code: int
    reason: str


# Queue overflow: closing beats dropping events silently (a lost collaborator.revoked would leave the stream open).
FORCE_RESYNC = CloseSignal(WS_CLOSE_RESYNC, "events dropped, resync")
# Logout: the JWT cannot be invalidated, but its sockets can be closed.
LOGGED_OUT = CloseSignal(WS_CLOSE_SESSION_EXPIRED, "logged out")


class EventBroker:
    """In-memory pub/sub for real-time workspace events, keyed by workspace id.

    Transport-agnostic: the realtime WebSocket gateway (app/api/collab.py) drains each subscriber's
    queue and sends it over the socket. Each subscriber gets its own asyncio.Queue, fanned out
    synchronously via `put_nowait` (so `publish` needs no await). Subscribers are also indexed by
    user so a logout can close every socket of that user.

    DEPLOYMENT CONSTRAINT — run a SINGLE worker process. The subscriber registry lives only in this
    process's memory, so an event published by one worker is never delivered to clients connected to a
    different worker. Running multiple workers (`uvicorn --workers N`, gunicorn `-w N`) or multiple
    replicas therefore *silently* drops cross-worker events and leaves those viewers stale until they
    reconnect. To scale horizontally, replace this fan-out with a shared backend (e.g. Redis pub/sub)
    behind the same subscribe/unsubscribe/publish surface. See the deployment note in README.md.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._by_user: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._seq = 0

    def subscribe(self, workspace_id: str, user_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[workspace_id].add(queue)
        self._by_user[user_id].add(queue)
        return queue

    def unsubscribe(self, workspace_id: str, queue: asyncio.Queue, user_id: str) -> None:
        self._discard(self._subscribers, workspace_id, queue)
        self._discard(self._by_user, user_id, queue)

    @staticmethod
    def _discard(registry: dict[str, set[asyncio.Queue]], key: str, queue: asyncio.Queue) -> None:
        queues = registry.get(key)
        if not queues:
            return
        queues.discard(queue)
        if not queues:
            registry.pop(key, None)

    def emit(self, workspace_id: str, event_type: EventType, **fields: Any) -> None:
        """Build and publish an event; see `build_event` for the fields."""
        self.publish(workspace_id, build_event(event_type, **fields))

    def publish(self, workspace_id: str, event: dict[str, Any]) -> None:
        """Fan an event out to every subscriber of a workspace. Non-blocking and never raises."""
        self._seq += 1
        envelope = {**event, "seq": self._seq, "ts": datetime.now(UTC).isoformat()}
        for queue in list(self._subscribers.get(workspace_id, ())):
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                logger.warning(
                    "Realtime queue full for workspace %s; forcing subscriber to disconnect and resync (dropped %s)",
                    workspace_id,
                    event.get("type"),
                )
                self._signal(queue, FORCE_RESYNC)

    def close_user_connections(self, user_id: str) -> int:
        """Tell every socket of a user to close (logout). Returns how many were signalled."""
        queues = list(self._by_user.get(user_id, ()))
        for queue in queues:
            self._signal(queue, LOGGED_OUT)
        return len(queues)

    @staticmethod
    def _signal(queue: asyncio.Queue, signal: CloseSignal) -> None:
        """Drain the backlog so the signal fits, then enqueue it (nothing else runs in between)."""
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(signal)


# Module-level singleton shared across all requests in the process.
event_broker = EventBroker()
