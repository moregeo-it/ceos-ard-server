import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_file_service
from app.schemas.error import create_error_detail
from app.schemas.workspace import (
    ChangedFilesResponse,
    CreateFileRequest,
    FileListResponse,
    FileOperationResponse,
    FilePatchRequest,
    FileSearchResponse,
    FileContextResponse,
)
from app.services.auth_service import require_github_user
from app.services.file_service import FileService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Files"])


@router.get(
    "/{workspace_id}/files",
    summary="List files in a workspace",
    description="List files and folders in a workspace",
    response_model=list[FileListResponse],
    status_code=status.HTTP_200_OK,
)
async def list_workspace_files(
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
    path: str | None = Query(default="/", description="Path to list files from"),
    recurse: bool = Query(default=False, description="Whether to list files recursively"),
):
    try:
        return await file_service.get_workspace_files(db=db, path=path, workspace_id=workspace_id, user_id=current_user["user"].id, recurse=recurse)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing workspace files: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("list workspace files", e)) from e


@router.post(
    "/{workspace_id}/files",
    summary="Create a file or folder in a workspace",
    description="Create a file or folder in a workspace",
    response_model=FileOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    workspace_id: str,
    create_file_request: CreateFileRequest,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        return await file_service.create(db=db, workspace_id=workspace_id, request_data=create_file_request, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating file or folder: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("create file or folder", e)) from e


@router.get(
    "/{workspace_id}/files/{file_path:path}",
    summary="Read content of a file",
    description="Read content of a file",
)
async def read_file_content(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        file_info = await file_service.read_file_content(db=db, workspace_id=workspace_id, file_path=file_path, user_id=current_user["user"].id)

        return Response(content=file_info["content"], media_type=file_info["media_type"], status_code=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error reading file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("read file", e)) from e


@router.put(
    "/{workspace_id}/files/{file_path:path}",
    summary="Store content of a file",
    description="Store content of a file",
    response_model=FileListResponse,
    status_code=status.HTTP_200_OK,
)
async def store_file_content(
    request: Request,
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        return await file_service.store_file_content(
            db=db,
            workspace_id=workspace_id,
            file_path=file_path,
            content=await request.body(),
            user_id=current_user["user"].id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error storing file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("store file", e)) from e


@router.delete(
    "/{workspace_id}/files/{file_path:path}",
    summary="Delete a file or folder in a workspace",
    description="Delete a file or folder in a workspace",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        return await file_service.delete(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("delete file", e)) from e


@router.patch(
    "/{workspace_id}/files/{file_path:path}",
    summary="Update file metadata or operations",
    description="Perform file operations such rename or revert changes",
    response_model=FileOperationResponse,
    status_code=status.HTTP_200_OK,
)
async def patch_file(
    file_path: str,
    workspace_id: str,
    operation_request: FilePatchRequest,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        return await file_service.update_file(
            db=db,
            file_path=file_path,
            workspace_id=workspace_id,
            operation_request=operation_request,
            user_id=current_user["user"].id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("update file", e)) from e


@router.get(
    "/{workspace_id}/search",
    summary="Search files in a workspace",
    description="search through all files in a workspace",
    response_model=list[FileSearchResponse],
    status_code=status.HTTP_200_OK,
)
async def search_files(
    workspace_id: str,
    query: str = Query(description="Search terms to look for in files and folders"),
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        return await file_service.search_files(db=db, workspace_id=workspace_id, search_query=query, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching files: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("search files", e)) from e


@router.get(
    "/{workspace_id}/diffs",
    summary="Get list of changed files",
    description="Retrieve a list of changed files in a workspace - includes untracked files",
    response_model=list[ChangedFilesResponse],
    status_code=status.HTTP_200_OK,
)
async def get_changed_files(
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        return await file_service.get_changed_files(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting changed files: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("get changed files", e)) from e


@router.get(
    "/{workspace_id}/diffs/{file_path:path}",
    summary="Get diff for a specific file",
    description="Retrieve the diff for a specific file in a workspace",
)
async def get_file_diff(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        file_diff = await file_service.get_file_diff(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)

        return Response(content=file_diff, media_type="text/plain; charset=utf-8", status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error getting file diff: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("get file diff", e)) from e

@router.get(
    "/{workspace_id}/context/{file_path:path}",
    summary="Get aditional context for a specific file",
    description="Retrieve additional context for a specific file in a workspace",
    response_model=FileContextResponse,
    status_code=status.HTTP_200_OK,
)
async def get_file_context(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    try:
        file_context = await file_service.get_file_context(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)
        return file_context
    except Exception as e:
        logger.error(f"Error getting file context: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("get file context", e)) from e
