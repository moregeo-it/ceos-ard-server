import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.services import collab_service
from app.services.jwt_service import JWTService
from app.services.share_service import ROLE_RANK
from app.services.workspace_service import WorkspaceService
from app.utils.validation import validate_workspace_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Collab"])

workspace_service = WorkspaceService()


async def _authenticate(websocket: WebSocket, db: Session) -> User | None:
    """Resolve the connecting user from the `authorization` query param.

    Browsers cannot set custom headers on a WebSocket handshake, so this mirrors the
    query-param fallback already used by `auth_service.get_jwt_token` for the same reason.
    """
    token = websocket.query_params.get("authorization")
    if not token:
        return None
    try:
        payload = JWTService.decode_access_token(token)
    except HTTPException:
        return None
    user_id = payload.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


@router.websocket("/{workspace_id}/collab/{file_path:path}")
async def collaborate_on_file(websocket: WebSocket, workspace_id: str, file_path: str, db: Session = Depends(get_db)):
    user = await _authenticate(websocket, db)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        workspace = workspace_service.get_workspace_by_id(db, workspace_id, user.id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    is_editor = ROLE_RANK.get(workspace.viewer_role, -1) >= ROLE_RANK["edit"]

    normalized_path = file_path if file_path.startswith("/") else f"/{file_path}"
    try:
        abs_file_path = validate_workspace_path(normalized_path, workspace.abs_path, type="file")
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    channel = collab_service.FastAPIWebsocketChannel(websocket, path=collab_service.room_key(workspace_id, normalized_path))
    try:
        await collab_service.serve(workspace_id, normalized_path, workspace.abs_path, abs_file_path, channel, is_editor)
    except WebSocketDisconnect:
        pass
