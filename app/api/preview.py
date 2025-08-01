from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Dict, Any

import logging

from app.db.database import get_db
from app.services.auth_service import get_current_user
from app.services.preview_service import preview_service
from app.schemas.preview import PreviewListResponse, PreviewErrorMessage, PreviewFileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preview", tags=["Preview"])

@router.get(
    "/list/{workspace_id}", 
    response_model=PreviewListResponse, 
    responses={
        404: {"model": PreviewErrorMessage},
        500: {"model": PreviewErrorMessage}
    }
)
async def list_preview(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):

    if not workspace_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Workspace ID is required"},
        )

    try:
        success, preview_files, error_message = await preview_service.list_preview_files(
            db=db,
            workspace_id=workspace_id,
            user_id=current_user["user"].id
        )

        if not success:
            if 'Build directory does not exist. Please build the workspace first.' in error_message:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={PreviewErrorMessage(message=error_message).dict}, 
                )
            else:
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={PreviewErrorMessage(message=error_message).dict},
                )

        return JSONResponse(
            status_code=status.HTTP_200_OK, 
            content={
                PreviewListResponse(
                    success=success, 
                    preview_files=preview_files, 
                    message=error_message).dict()
                }
            )

    except Exception as e:
        logger.error(f"Error getting preview list for workspace {workspace_id}: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={PreviewErrorMessage(message=str(e)).dict},
        )
    
@router.get(
    "/file-content/{workspace_id}/{file_name}", 
    response_model=PreviewFileResponse,
    responses={
        404: {"model": PreviewErrorMessage},
        500: {"model": PreviewErrorMessage}
    }
)
async def get_preview_file(
    workspace_id: str,
    file_name: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    if not workspace_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Workspace ID is required"},
        )

    if not file_name:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "File name is required"},
        )

    try:
        success, preview_file, error_message = await preview_service.get_preview_file(
            db=db,
            file_name=file_name,
            workspace_id=workspace_id,
            user_id=current_user["user"].id
        )

        if not success:
            if 'Build directory does not exist. Please build the workspace first.' in error_message:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={PreviewErrorMessage(message=error_message).dict}, 
                )
            else:
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={PreviewErrorMessage(message=error_message).dict},
                )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                PreviewFileResponse(
                    success=success, 
                    preview_file=preview_file, 
                    message=error_message).dict()
                }
            )

    except Exception as e:
        logger.error(f"Error getting preview file {file_name} for workspace {workspace_id}: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={PreviewErrorMessage(message=str(e)).dict},
        )