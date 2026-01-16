import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, FileResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_preview_service
from app.schemas.error import create_error_detail
from app.services.auth_service import require_github_user
from app.services.preview_service import PreviewService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Preview"])


@router.get(
    "/{workspace_id}/previews",
    summary="Generate Previews",
    description="Generate Previews for a workspace",
    status_code=status.HTTP_200_OK,
)
async def generate_preview(
    workspace_id: str,
    db: Session = Depends(get_db),
    pfs: list[str] | None = Query(default=None, min_items=1, max_items=50),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    try:
        generated_previews = await preview_service.generate_preview(db=db, pfs=pfs, workspace_id=workspace_id, user_id=current_user["user"].id)

        return Response(content=generated_previews, status_code=status.HTTP_200_OK, media_type="text/html")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("generate preview", e),
        ) from e


@router.get(
    "/{workspace_id}/previews/{file_path:path}",
    summary="Get preview static file asset",
    description="Get preview static file asset for a workspace",
    status_code=status.HTTP_200_OK,
)
async def get_preview_static_file(
    workspace_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    try:
        file = await preview_service.get_preview_static_file(
            db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id
        )

        return FileResponse(str(file))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preview static file {file_path} for workspace {workspace_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("get preview static file", e),
        ) from e
