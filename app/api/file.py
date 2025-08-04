import os
import pathlib
from fastapi import APIRouter, Depends, status, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Dict, Any

import logging

from app.db.database import get_db
from app.utils.sanitization import sanitize_path
from app.services.git_service import git_service
from app.services.file_service import file_service
from app.services.auth_service import get_current_user
from app.services.workspace_service import workspace_service
from app.schemas.workspace import CreateFileRequest, FileOperationRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["File"])

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
        files_and_folder = file_service.get_workspace_files(
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
            status_code=status.HTTP_201_OK
        )
    except Exception as e:
        logger.error(f"Error creating file or folder: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create file or folder: {str(e)}"
        )

@router.get(
    "/{workspace_id}/files/{file_path}", 
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

        return JSONResponse(
            content=file_content,
            status_code=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Error reading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read file: {str(e)}"
        )

@router.put(
    "/{workspace_id}/files/{file_path}", 
    summary="Store content of a file",
    description="Store content of a file",
    )
async def store_file_content(
    content: str,
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        file_stored = await file_service.store_file_content(
            db=db, 
            workspace_id=workspace_id, 
            file_path=file_path, 
            content=content, 
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
    "/{workspace_id}/files/{file_path}",
    summary="Delete a file or folder in a workspace",
    description="Delete a file or folder in a workspace",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_fileor_folder(
    type: str,
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        await file_service.delete_file_or_folder(
            db=db, 
            type=type,
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
    "/{workspace_id}/files/{file_path}",
    summary="Updatea file metadata or operations",
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

@router.post("/revert-changes")
async def revert_file_changes(
    file_path: str, 
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        if not file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "File path is required"},
            )
        
        if not workspace_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Workspace ID is required"},
            )

        workspace = workspace_service.get_workspace_by_id(db, workspace_id, current_user["user"].id)
        workspace_path = str(workspace.workspace_path)

        full_path = os.path.join(workspace_path, file_path)
        if not full_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "Invalid file path"},
            )

        git_cmd = f"git checkout -- {file_path}"
        stdout, stderr, returncode = git_service.run_git_command(git_cmd, workspace_path)

        if returncode != 0:
            logger.error(f"Error reverting file changes: {stderr}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"success": False, "message": "Failed to revert file changes", "error": stderr},
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"success": True, "message": "File reverted successfully", "file_path": file_path},
        )
    except Exception as e:
        logger.error(f"Error reverting file changes: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Failed to revert file changes", "error": str(e)},
        )

@router.get("/diff")
async def get_diff(
    file_path: str, 
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        if not file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "File path is required"},
            )
        
        if not workspace_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Workspace ID is required"},
            )

        workspace = workspace_service.get_workspace_by_id(db, workspace_id, current_user["user"].id)
        workspace_path = str(workspace.workspace_path)

        full_path = os.path.join(workspace_path, file_path)
        if not full_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "Invalid file path"},
            )

        git_status_cmd = ["git", "status", "--porcelain", "--", file_path]
        stdout, stderr, returncode = git_service._run_git_command(git_status_cmd, workspace_path)

        if returncode != 0:
            logger.error(f"Error getting git status: {stderr}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"success": False, "message": "Failed to get git status", "error": stderr},
            )

        git_status_output = stdout.strip()

        if git_status_output == "":
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "diff": "No changes"},
            )

        status_code = git_status_output[:2].strip()

        if status_code == "A" or status_code == "??":
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "diff": f"New file: {file_path}"},
            )
        elif status_code == "D" or "D" in status_code:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "diff": f"File deleted: {file_path}"},
            )
        elif "R" in status_code:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "diff": f"File renamed: {file_path}"},
            )
        else:
            git_diff_cmd = ["git", "diff", "--", file_path]
            stdout, stderr, returncode = git_service._run_git_command(git_diff_cmd, workspace_path)

            if returncode != 0:
                logger.error(f"Error getting git diff: {stderr}")
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"success": False, "message": "Failed to get git diff", "error": stderr},
                )

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "diff": stdout},
            )
    except Exception as e:
        logger.error(f"Error getting diff: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Failed to get diff", "error": str(e)},
        )