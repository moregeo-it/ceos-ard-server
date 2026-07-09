import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_event_broker, get_workspace_service
from app.schemas.events import EventType
from app.services.auth_service import require_github_user
from app.services.events_service import HEARTBEAT_SECONDS, EventBroker
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Events"])

# Event types that terminate the stream once delivered (the viewer has lost access or the
# workspace is gone), so the client stops reconnecting.
_CLOSING_EVENTS = {EventType.SHARE_REVOKED.value, EventType.WORKSPACE_DELETED.value}


@router.get(
    "/{workspace_id}/events",
    summary="Subscribe to real-time workspace change events (SSE)",
    description="Server-Sent Events stream of file/workspace changes for any user with access to the workspace.",
)
async def workspace_events(
    workspace_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    broker: EventBroker = Depends(get_event_broker),
):
    # Authorize: any user with access (owner or readonly) may subscribe. Raises 404 if the user
    # has no access at all, 403 if below the readonly bar (never for the default readonly min_role).
    user_id = current_user["user"].id
    workspace_service.get_workspace_by_id(db, workspace_id, user_id)

    queue = broker.subscribe(workspace_id)

    async def event_stream():
        try:
            # Prime the connection so the client's onopen fires promptly.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                target_user_id = event.get("target_user_id")
                if target_user_id is not None and target_user_id != user_id:
                    continue

                payload = json.dumps(event, default=str)
                yield f"event: {event['type']}\nid: {event['seq']}\ndata: {payload}\n\n"

                if event["type"] in _CLOSING_EVENTS:
                    break
        except asyncio.CancelledError:
            # Client disconnected mid-await; fall through to cleanup.
            raise
        finally:
            broker.unsubscribe(workspace_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (e.g. nginx) so events are flushed immediately.
            "X-Accel-Buffering": "no",
        },
    )
