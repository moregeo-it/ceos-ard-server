import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.workspace import (
    CreatePFSRequest,
    ProposeChangesRequest,
    ProposeChangesResponse,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceStatusResponse,
    WorkspaceUpdate,
)
from app.services.auth_service import get_current_user
from app.services.workspace_service import workspace_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


@router.post("/", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED, description="Create a new workspace with cloned repository")
async def create_workspace(
    workspace_data: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = current_user["user"].id
    username = current_user["user"].username
    access_token = current_user["access_token"]

    try:
        return await workspace_service.create_workspace(
            db=db, user_id=user_id, username=username, workspace_data=workspace_data, access_token=access_token
        )

    except Exception as e:
        logger.error(f"Error creating workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create workspace: {str(e)}") from None


@router.get(
    "/",
    summary="List all workspaces",
    status_code=status.HTTP_200_OK,
    response_model=list[WorkspaceResponse],
    description="Retrieve all workspaces for a authenticated user",
)
async def get_user_workspaces(
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return workspace_service.get_user_workspaces(db=db, user_id=current_user["user"].id)
    except Exception as e:
        logger.error(f"Error getting workspaces: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspaces: {str(e)}") from None


@router.get(
    "/{workspace_id}",
    summary="Get a workspace",
    response_model=WorkspaceResponse,
    description="Retrieve detailed information about a specific workspace",
)
async def get_user_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return workspace_service.get_workspace_by_id(
            db=db, check_pr=True, workspace_id=workspace_id, user_id=current_user["user"].id, access_token=current_user["access_token"]
        )

    except Exception as e:
        logger.error(f"Error getting workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace: {str(e)}") from None


@router.patch("/{workspace_id}", summary="Update a workspace", response_model=WorkspaceResponse, description="Update a workspace information")
async def update_workspace(
    workspace_id: str,
    update_data: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await workspace_service.update_workspace(db=db, workspace_id=workspace_id, user_id=current_user["user"].id, update_data=update_data)

    except Exception as e:
        logger.error(f"Error updating workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update workspace: {str(e)}") from None


@router.delete(
    "/{workspace_id}", summary="Delete a workspace", status_code=status.HTTP_204_NO_CONTENT, description="Delete a workspace and all associated data"
)
async def delete_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await workspace_service.delete_workspace(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)

    except Exception as e:
        logger.error(f"Error deleting workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete workspace: {str(e)}") from None


@router.get("/{workspace_id}/status", response_model=WorkspaceStatusResponse)
async def get_workspace_status(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await workspace_service.get_workspace_status(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)

    except Exception as e:
        logger.error(f"Error getting workspace status: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace status: {str(e)}") from None


@router.post("/{workspace_id/propose}", response_model=ProposeChangesResponse)
async def propose_changes(
    workspace_id: str,
    propose_data: ProposeChangesRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        access_token = current_user.get("access_token")

        return await workspace_service.propose_changes(
            db=db,
            workspace_id=workspace_id,
            user_id=current_user["user"].id,
            pr_title=propose_data.pr_title,
            pr_description=propose_data.pr_description,
            access_token=access_token,
        )

    except Exception as e:
        logger.error(f"Error proposing changes: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to propose changes: {str(e)}") from None


@router.get("/{workspace_id}/pfs", summary="List PFS types", description="List PFS types of a workspace", tags=["PFS"])
async def list_workspace_pfs_types(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await workspace_service.get_workspace_pfs_types(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)

    except Exception as e:
        logger.error(f"Error listing Workspace PFS types: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list PFS types: {str(e)}") from None


@router.post("/{workspace_id}/pfs", summary="Create a PFS", description="Create a PFS of a workspace", tags=["PFS"])
async def create_workspace_pfs(
    workspace_id: str,
    create_pfs_request: CreatePFSRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await workspace_service.create_workspace_pfs(
            db=db, workspace_id=workspace_id, user_id=current_user["user"].id, create_pfs_request=create_pfs_request
        )

    except Exception as e:
        logger.error(f"Error creating PFS: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create PFS: {str(e)}") from None
