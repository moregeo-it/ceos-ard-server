import asyncio
import json
import logging
import time
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, WebSocket, status

from app.config import settings
from app.db.database import SessionLocal
from app.dependencies import get_event_broker, get_workspace_service
from app.schemas.events import EventType
from app.services.auth_service import get_current_user, require_github_user
from app.services.events_service import HEARTBEAT_SECONDS, WS_CLOSE_ACCESS_REVOKED, WS_CLOSE_SESSION_EXPIRED, CloseSignal
from app.services.jwt_service import JWTService
from app.utils.request_context import CLIENT_ID_QUERY_PARAM, validate_client_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Realtime"])

# Delivering one of these ends the connection: the viewer's access is gone, so it shouldn't reconnect.
# This is also what makes mid-session revocation close the socket - `share.revoked` is targeted at the
# revoked user (see share_service.revoke_share), so only their connection receives it and then closes.
_CLOSING_EVENTS = {EventType.SHARE_REVOKED.value, EventType.WORKSPACE_DELETED.value}

# Gateway-only fields, stripped before sending.
_INTERNAL_FIELDS = frozenset({"actor_client_id"})


class _Rejected(Exception):
    """Handshake failure with the close code to report."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


async def _authenticate(token: str | None, db) -> tuple[str, float]:
    """Validate the JWT; returns the user id and the token's expiry (unix time)."""
    if not token:
        raise _Rejected(WS_CLOSE_SESSION_EXPIRED, "missing token")
    try:
        expires_at = float(JWTService.decode_access_token(token)["exp"])
        current_user = await get_current_user(token=token, db=db)
    except HTTPException as e:
        raise _Rejected(WS_CLOSE_SESSION_EXPIRED, "invalid or expired token") from e
    try:
        await require_github_user(current_user=current_user)
    except HTTPException as e:
        raise _Rejected(WS_CLOSE_ACCESS_REVOKED, "GitHub account required") from e
    return current_user["user"].id, expires_at


async def _reject(websocket: WebSocket, code: int, reason: str) -> None:
    """Accept, then close: a close before the upgrade reaches browsers only as code 1006, hiding 4001/4003."""
    await websocket.accept()
    await websocket.close(code=code, reason=reason)


def _deliverable(event: dict[str, Any], user_id: str, client_id: str | None) -> bool:
    target_user_id = event.get("target_user_id")
    if target_user_id is not None and target_user_id != user_id:
        return False
    # Echo filter: only the tab that made the change is skipped, so user and client id must both match.
    if client_id is not None and event.get("actor_user_id") == user_id and event.get("actor_client_id") == client_id:
        return False
    return True


@router.websocket("/{workspace_id}/ws")
async def workspace_ws(websocket: WebSocket, workspace_id: str):
    """Real-time workspace event stream over WebSocket.

    Handshake: Origin check → JWT from the `authorization` query param (browsers can't set a header
    on a handshake; a session cookie will replace it, see #98) → subscribe → access check → accept.
    Subscribing before the access check keeps a revocation committing in between from being missed.
    Failures after the origin check are reported as close codes 4001/4003.
    """
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.CORS_ORIGINS:
        # Browsers always send Origin; a foreign page just gets a failed upgrade.
        logger.warning("Rejected realtime connection from origin %s for workspace %s", origin, workspace_id)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token = websocket.query_params.get("authorization")
    client_id = validate_client_id(websocket.query_params.get(CLIENT_ID_QUERY_PARAM))
    broker = get_event_broker()

    # Session for the handshake only.
    db = SessionLocal()
    try:
        try:
            user_id, token_expires_at = await _authenticate(token, db)
        except _Rejected as e:
            await _reject(websocket, e.code, e.reason)
            return

        queue = broker.subscribe(workspace_id, user_id)
        try:
            get_workspace_service().get_workspace_by_id(db, workspace_id, user_id)
        except HTTPException:
            broker.unsubscribe(workspace_id, queue, user_id)
            await _reject(websocket, WS_CLOSE_ACCESS_REVOKED, "no access to this workspace")
            return
    finally:
        db.close()

    await websocket.accept()

    try:
        # A task group runs the outbound writer and the inbound reader concurrently; whichever ends
        # first cancels the other. Using anyio (not raw asyncio tasks) keeps cancellation aligned
        # with Starlette's own WebSocket cancel scope so the handler unwinds cleanly on disconnect.
        async with anyio.create_task_group() as tg:

            async def close_and_stop(code: int, reason: str) -> None:
                """Close with a code the client can see, then cancel the other task."""
                try:
                    await websocket.close(code=code, reason=reason)
                except RuntimeError:
                    pass  # already closing
                tg.cancel_scope.cancel()

            async def writer() -> None:
                """Deliver queue items; ping when idle; close when the JWT expires."""
                while True:
                    remaining = token_expires_at - time.time()
                    if remaining <= 0:
                        await close_and_stop(WS_CLOSE_SESSION_EXPIRED, "token expired")
                        return
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=min(HEARTBEAT_SECONDS, remaining))
                    except TimeoutError:
                        if token_expires_at - time.time() > 0:
                            await websocket.send_text(json.dumps({"type": "ping"}))
                        continue

                    if isinstance(item, CloseSignal):
                        await close_and_stop(item.code, item.reason)
                        return

                    if not _deliverable(item, user_id, client_id):
                        continue

                    await websocket.send_text(json.dumps({k: v for k, v in item.items() if k not in _INTERNAL_FIELDS}, default=str))

                    if item["type"] in _CLOSING_EVENTS:
                        # Terminal event delivered; end the connection.
                        await close_and_stop(WS_CLOSE_ACCESS_REVOKED, "access revoked")
                        return

            async def reader() -> None:
                """Clients send nothing: any data frame closes the socket (oversized frames are rejected by uvicorn with 1009)."""
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        tg.cancel_scope.cancel()
                        return
                    logger.warning("Realtime client sent data on workspace %s; closing", workspace_id)
                    await close_and_stop(status.WS_1008_POLICY_VIOLATION, "clients must not send data")
                    return

            tg.start_soon(writer)
            tg.start_soon(reader)
    finally:
        broker.unsubscribe(workspace_id, queue, user_id)
