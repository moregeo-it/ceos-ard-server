import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from app.db.database import SessionLocal
from app.models.user import User
from app.services import collab_service
from app.services.jwt_service import JWTService
from app.services.share_service import ROLE_RANK
from app.services.workspace_service import WorkspaceService
from app.utils.validation import validate_workspace_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Collab"])

_workspace_service = WorkspaceService()


def _authorize(token: str | None, workspace_id: str, file_path: str) -> tuple:
    """Authenticate + authorize a collab connection using a short-lived DB session.

    Returns ``(is_editor, workspace_abs_path, abs_file_path, normalized_path)`` or raises
    ``HTTPException``. The DB is only needed here (at connect); we deliberately do NOT hold a
    request-scoped session for the whole socket lifetime (that would keep an idle SQLite connection
    open per editor tab).
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization token")
    db = SessionLocal()
    try:
        payload = JWTService.decode_access_token(token)
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        # Any collaborator/owner (>= readonly) may connect; no access -> 404, insufficient -> 403.
        workspace = _workspace_service.get_workspace_by_id(db, workspace_id, user.id)
        normalized_path = file_path if file_path.startswith("/") else f"/{file_path}"
        abs_file_path = validate_workspace_path(normalized_path, workspace.abs_path, type="file")
        is_editor = ROLE_RANK.get(workspace.viewer_role, -1) >= ROLE_RANK["edit"]
        return is_editor, workspace.abs_path, abs_file_path, normalized_path
    finally:
        db.close()


@router.websocket("/{workspace_id}/collab/{file_path:path}")
async def collaborate_on_file(websocket: WebSocket, workspace_id: str, file_path: str):
    """Real-time collaborative editing of a single file via `@codemirror/collab` (OT authority).

    The JWT is passed as the ``authorization`` query param because browsers cannot set headers on
    a WebSocket handshake.
    """
    token = websocket.query_params.get("authorization")
    try:
        is_editor, workspace_abs_path, abs_file_path, normalized_path = _authorize(token, workspace_id, file_path)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    key = collab_service.room_key(workspace_id, normalized_path)
    base_doc = abs_file_path.read_text(encoding="utf-8", errors="replace") if abs_file_path.exists() else ""
    room = collab_service.get_or_create_room(key, base_doc, workspace_abs_path, abs_file_path)

    conn = collab_service.Connection(websocket, is_editor)
    await room.join(conn)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(message, dict):
                await room.handle(conn, message)
    except WebSocketDisconnect:
        pass
    finally:
        room.leave(conn)
        # Flushes the latest snapshot to git before dropping the room, so a later reader sees it.
        await collab_service.discard_room_if_empty(key)
