from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from typing import Dict, Any

import logging

from app.db.database import get_db
from app.services.file_service import file_service
from app.services.auth_service import get_current_user
from app.schemas.workspace import CreateFileRequest, FileOperationRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Files"])

@router.get(
    "/{workspace_id}/files",
    summary="List files in a workspace",
    description="List files and folders in a workspace",
)
async def list_workspace_files(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    try:
        files_and_folder = await file_service.get_workspace_files(
            db=db, 
            workspace_id=workspace_id, 
            user_id=current_user["user"].id
        )

        return JSONResponse(
            content=files_and_folder,
            status_code=status.HTTP_200_OK
        )
    except Exception as e:
        logger.error(f"Error listing workspace files: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list workspace files: {str(e)}"
        )
    
@router.post(
    "/{workspace_id}/files",
    summary="Create a file or folder in a workspace",
    description="Create a file or folder in a workspace",
)
async def create_file_or_folder(
    workspace_id: str,
    create_file_request: CreateFileRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        file_or_folder = await file_service.create_file_or_folder(
            db=db, 
            workspace_id=workspace_id, 
            request_data=create_file_request,
            user_id=current_user["user"].id
        )

        return JSONResponse(
            content=file_or_folder,
            status_code=status.HTTP_201_CREATED
        )
    except Exception as e:
        logger.error(f"Error creating file or folder: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create file or folder: {str(e)}"
        )

@router.get(
    "/{workspace_id}/files/{file_path:path}", 
    summary="Read content of a file",
    description="Read content of a file",
)
async def read_file_content(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        file_content = await file_service.read_file_content(
            db=db, 
            workspace_id=workspace_id, 
            file_path=file_path, 
            user_id=current_user["user"].id
        )

        return Response(
            content=file_content,
            media_type="application/octet-stream",
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Error reading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read file: {str(e)}"
        )

@router.put(
    "/{workspace_id}/files/{file_path:path}", 
    summary="Store content of a file",
    description="Store content of a file",
    )
async def store_file_content(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    content: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        file_stored = await file_service.store_file_content(
            db=db, 
            workspace_id=workspace_id, 
            file_path=file_path, 
            content=await content.read(), 
            user_id=current_user["user"].id
        )

        return JSONResponse(
            content=file_stored,
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Error storing file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store file: {str(e)}"
        )

@router.delete(
    "/{workspace_id}/files/{file_path:path}",
    summary="Delete a file or folder in a workspace",
    description="Delete a file or folder in a workspace",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_fileor_folder(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        await file_service.delete_file_or_folder(
            db=db,
            file_path=file_path, 
            workspace_id=workspace_id,
            user_id=current_user["user"].id
        )
    
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(e)}"
        )
    
@router.patch(
    "/{workspace_id}/files/{file_path:path}",
    summary="Update file metadata or operations",
    description="Perform file operations such rename or revert changes",
)
async def patch_file(
    file_path: str, 
    workspace_id: str,
    operation_request: FileOperationRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        file_updated = await file_service.update_file(
            db=db, 
            file_path=file_path, 
            workspace_id=workspace_id,
            operation_request=operation_request, 
            user_id=current_user["user"].id
        )

        return JSONResponse(
            content=file_updated,
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Error renaming file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rename file: {str(e)}"
        )

@router.get(
    "/{workspace_id}/search",
    summary="Search files in a workspace",
    description="search through all files in a workspace",
)
async def search_files(
    workspace_id: str,
    search_query: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        files_and_folder = await file_service.search_files(
            db=db, 
            workspace_id=workspace_id, 
            search_query=search_query, 
            user_id=current_user["user"].id
        )

        return JSONResponse(
            content=files_and_folder,
            status_code=status.HTTP_200_OK
        )
    except Exception as e:
        logger.error(f"Error searching files: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search files: {str(e)}"
        )
    
@router.get(
    "/{workspace_id}/diffs",
    summary="Get list of changed files",
    description="Retrieve a list of changed files in a workspace - includes untracked files",
)
async def get_changed_files(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        changed_files = await file_service.get_changed_files(
            db=db, 
            workspace_id=workspace_id, 
            user_id=current_user["user"].id
        )

        return JSONResponse(
            content=changed_files,
            status_code=status.HTTP_200_OK
        )
    except Exception as e:
        logger.error(f"Error getting changed files: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get changed files: {str(e)}"
        )
    
@router.get(
    "/{workspace_id}/diffs/{file_path:path}",
    summary="Get diff for a specific file",
    description="Retrieve the diff for a specific file in a workspace",
)
async def get_file_diff(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        file_diff = await file_service.get_file_diff(
            db=db, 
            file_path=file_path, 
            workspace_id=workspace_id, 
            user_id=current_user["user"].id
        )

        return Response(
            content=file_diff,
            media_type="text/plain; charset=utf-8",
            status_code=status.HTTP_200_OK
        )
    except Exception as e:
        logger.error(f"Error getting file diff: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file diff: {str(e)}"
        )