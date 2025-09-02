import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_preview_service
from app.schemas.preview import PreviewErrorMessage
from app.services.auth_service import get_current_user
from app.services.preview_service import PreviewService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Preview"])


@router.get(
    "/{workspace_id}/previews",
    summary="Generate Previews",
    description="Generate Previews for a workspace",
)
async def generate_preview(
    workspace_id: str,
    db: Session = Depends(get_db),
    pfs: list[str] | None = Query(default=None, min_items=1, max_items=50),
    current_user: dict[str, Any] = Depends(get_current_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    try:
        generated_previews = await preview_service.generate_preview(db=db, pfs=pfs, workspace_id=workspace_id, user_id=current_user["user"].id)

        return Response(content=generated_previews, status_code=status.HTTP_200_OK, media_type="text/html")

    except Exception as e:
        logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={PreviewErrorMessage(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(e))},
        )
