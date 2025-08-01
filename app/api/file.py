import os
import pathlib
from fastapi import APIRouter, Depends, status, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Dict, Any

import logging

from app.db.database import get_db
from app.utils.sanitization import sanitize_path
from app.services.auth_service import get_current_user
from app.services.workspace_service import workspace_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/file", tags=["File"])

@router.get("/read-content")
async def read_content(
    file_path: str, 
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        if not file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is required"},
            )

        if not workspace_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Workspace ID is required"},
            )

        workspace = workspace_service.get_workspace_by_id(db, workspace_id, current_user["user"].id)
        workspace_path = str(workspace.workspace_path)

        file_path = sanitize_path(file_path)
        file_path = os.path.join(workspace_path, file_path)
        if not file_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is not in the workspace"},
            )

        if not os.path.exists(file_path):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "File not found"},
            )

        if not os.path.isfile(file_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Path does not point to a file"},
            )

        file_extention = pathlib.Path(file_path).suffix
        editable_extentions = [".md", ".yaml", ".yml", ".bib", "", ".txt"]

        if file_extention not in editable_extentions:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File is not editable"},
            )
        
        content = ""
        with open(file_path, "r") as f:
            content = f.read()

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "isEditable": True,
                "content": content,
                "file_path": file_path,
                "file_extention": file_extention
            }
        )

    except Exception as e:
        logger.error(f"Error reading file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read file: {str(e)}"
        )

@router.post("/store-content")
async def store_content(
    file_path: str, 
    content: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        if not file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is required"},
            )

        if not workspace_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Workspace ID is required"},
            )
        
        if not content:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Content is required"},
            )

        workspace = workspace_service.get_workspace_by_id(db, workspace_id, current_user["user"].id)
        workspace_path = str(workspace.workspace_path)

        file_path = sanitize_path(file_path)
        file_path = os.path.join(workspace_path, file_path)
        if not file_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is not in the workspace"},
            )

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "file_path": file_path,
                "message": "File stored successfully"
            }
        )

    except Exception as e:
        logger.error(f"Error storing file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store file: {str(e)}"
        )

@router.delete("/delete")
async def delete_file(
    file_path: str, 
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        if not file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is required"},
            )

        if not workspace_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Workspace ID is required"},
            )

        workspace = workspace_service.get_workspace_by_id(db, workspace_id, current_user["user"].id)
        workspace_path = str(workspace.workspace_path)

        file_path = sanitize_path(file_path)
        file_path = os.path.join(workspace_path, file_path)
        if not file_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is not in the workspace"},
            )

        if not os.path.exists(file_path):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "File not found"},
            )

        os.remove(file_path)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "file_path": file_path,
                "message": "File deleted successfully"
            }
        )
    
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(e)}"
        )
    
@router.patch("/rename")
async def rename_file(
    file_path: str, 
    new_file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        if not file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is required"},
            )

        if not workspace_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "Workspace ID is required"},
            )

        if not new_file_path:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "New file path is required"},
            )

        workspace = workspace_service.get_workspace_by_id(db, workspace_id, current_user["user"].id)
        workspace_path = str(workspace.workspace_path)

        file_path = sanitize_path(file_path)
        file_path = os.path.join(workspace_path, file_path)
        if not file_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "File path is not in the workspace"},
            )

        new_file_path = sanitize_path(new_file_path)
        new_file_path = os.path.join(workspace_path, new_file_path)
        if not new_file_path.startswith(workspace_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "New file path is not in the workspace"},
            )

        if not os.path.exists(file_path):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "File not found"},
            )
        
        if os.path.exists(new_file_path):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "New file path already exists"},
            )
        
        os.makedirs(os.path.dirname(new_file_path), exist_ok=True)

        os.rename(file_path, new_file_path)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "file_path": new_file_path,
                "message": "File renamed successfully"
            }
        )

    except Exception as e:
        logger.error(f"Error renaming file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rename file: {str(e)}"
        )