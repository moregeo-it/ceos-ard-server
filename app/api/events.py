import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.db.database import get_db
from app.dependencies import get_event_broker, get_workspace_service
from app.schemas.events import EventType
from app.services.auth_service import require_github_user
from app.services.events_service import FORCE_RESYNC, HEARTBEAT_SECONDS, EventBroker
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Events"])

# Delivering one of these ends the stream: the viewer's access is gone, so it shouldn't reconnect.
_CLOSING_EVENTS = {EventType.SHARE_REVOKED.value, EventType.WORKSPACE_DELETED.value}


@router.get(
    "/{workspace_id}/events",
    summary="Subscribe to real-time workspace change events (SSE)",
    description="Server-Sent Events stream of file/workspace changes for any user with access to the workspace.",
)
async def workspace_events(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    broker: EventBroker = Depends(get_event_broker),
):
    # Any user with access may subscribe; raises 404 if they have none.
    user_id = current_user["user"].id
    workspace_service.get_workspace_by_id(db, workspace_id, user_id)

    queue = broker.subscribe(workspace_id)

    async def event_stream():
        # EventSourceResponse handles framing, keepalive pings, and disconnect; on disconnect it
        # closes this generator, so the `finally` unsubscribes.
        try:
            while True:
                event = await queue.get()

                if event is FORCE_RESYNC:
                    # Queue overflowed (see EventBroker._force_resync); close so the client resyncs.
                    break

                target_user_id = event.get("target_user_id")
                if target_user_id is not None and target_user_id != user_id:
                    continue

                yield ServerSentEvent(event=event["type"], id=str(event["seq"]), data=json.dumps(event, default=str))

                if event["type"] in _CLOSING_EVENTS:
                    break
        finally:
            broker.unsubscribe(workspace_id, queue)

    return EventSourceResponse(event_stream(), ping=HEARTBEAT_SECONDS)
