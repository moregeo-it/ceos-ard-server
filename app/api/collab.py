import asyncio
import json
import logging

import anyio
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from app.db.database import SessionLocal
from app.dependencies import get_event_broker
from app.schemas.events import EventType
from app.services.auth_service import get_current_user, require_github_user
from app.services.events_service import FORCE_RESYNC, HEARTBEAT_SECONDS
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Realtime"])

# Delivering one of these ends the connection: the viewer's access is gone, so it shouldn't reconnect.
# This is also what makes mid-session revocation close the socket - `share.revoked` is targeted at the
# revoked user (see share_service.revoke_share), so only their connection receives it and then closes.
_CLOSING_EVENTS = {EventType.SHARE_REVOKED.value, EventType.WORKSPACE_DELETED.value}

_workspace_service = WorkspaceService()


async def _authorize(token: str | None, workspace_id: str) -> str:
    """Authenticate and authorize a realtime connection, returning the user's id.

    A WebSocket route can't use FastAPI's HTTP `Depends`/`HTTPException` flow, so the same checks are
    run directly: validate the JWT (`require_github_user`) and confirm the user has access to the
    workspace (`get_workspace_by_id`). Raises ``HTTPException`` on failure. The DB session is opened
    only for the handshake, not held for the socket's lifetime.
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization token")
    db = SessionLocal()
    try:
        current_user = await get_current_user(token=token, db=db)
        current_user = await require_github_user(current_user=current_user)
        user_id = current_user["user"].id
        # Any user with access may subscribe; raises 404 if they have none.
        _workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        return user_id
    finally:
        db.close()


@router.websocket("/{workspace_id}/ws")
async def workspace_ws(websocket: WebSocket, workspace_id: str):
    """Real-time workspace event stream over WebSocket.

    Pushes lifecycle event envelopes (file.saved, file.committed, share.revoked, workspace.deleted,
    ...) to any user with access to the workspace. The JWT is passed as the ``authorization`` query
    param because browsers can't set headers on a WebSocket handshake.
    """
    token = websocket.query_params.get("authorization")
    try:
        user_id = await _authorize(token, workspace_id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    broker = get_event_broker()
    queue = broker.subscribe(workspace_id)

    try:
        # A task group runs the outbound writer and the inbound reader concurrently; whichever ends
        # first cancels the other. Using anyio (not raw asyncio tasks) keeps cancellation aligned
        # with Starlette's own WebSocket cancel scope so the handler unwinds cleanly on disconnect.
        async with anyio.create_task_group() as tg:

            async def writer() -> None:
                """Drain the subscriber queue to the socket, filtering per-user and honoring closing
                events. Sends a heartbeat ping when idle so proxies don't drop the connection."""
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    except TimeoutError:
                        await websocket.send_text(json.dumps({"type": "ping"}))
                        continue

                    if event is FORCE_RESYNC:
                        # Queue overflowed (see EventBroker._force_resync); close so the client resyncs.
                        break

                    target_user_id = event.get("target_user_id")
                    if target_user_id is not None and target_user_id != user_id:
                        continue

                    await websocket.send_text(json.dumps(event, default=str))

                    if event["type"] in _CLOSING_EVENTS:
                        # Access is gone (revoke / workspace deleted): delivered, now close.
                        break
                # Writer-initiated close (terminal event or forced resync): close the socket
                # explicitly so the client sees the close frame, then cancel the reader. (On a
                # client-initiated disconnect the reader path cancels us instead and never gets here,
                # so we don't close an already-gone socket.)
                try:
                    await websocket.close()
                except RuntimeError:
                    pass  # already closing
                tg.cancel_scope.cancel()

            async def reader() -> None:
                """Consume inbound frames only to detect disconnect; clients don't send anything."""
                try:
                    while True:
                        await websocket.receive_text()
                except WebSocketDisconnect:
                    tg.cancel_scope.cancel()

            tg.start_soon(writer)
            tg.start_soon(reader)
    finally:
        broker.unsubscribe(workspace_id, queue)
