from fastapi import HTTPException, status, APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any, List

import logging

from app.db.database import get_db
from app.services.auth_service import get_current_user
from app.services.workspace_service import workspace_service
from app.schemas.build import BuildStatusResponse, StartBuildRequest
from app.schemas.workspace import (
    WorkspaceUpdate,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceStatusResponse, 
    ProposeChangesResponse, 
    ProposeChangesRequest
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["Workspace"])

@router.post("/", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):

    github_token = current_user["access_token"]

    workspace = await workspace_service.create_workspace(
        db=db,
        workspace_data=workspace_data,
        user_id=current_user["user"].id,
        username=current_user["user"].username,
        access_token=github_token
    )

    return workspace
    
@router.get("/", response_model=List[WorkspaceResponse])
async def get_user_workspaces(
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):

    try:
        workspaces = workspace_service.get_user_workspaces(
            db=db, 
            user_id=current_user["user"].id
        )

        return workspaces

    except Exception as e:
        logger.error(f"Error getting workspaces: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get workspaces: {str(e)}"
        )
    
@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_user_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):

    try:
        workspace = workspace_service.get_workspace_by_id(
            db=db, 
            workspace_id=workspace_id, 
            user_id=current_user["user"].id
        )

        return workspace

    except Exception as e:
        logger.error(f"Error getting workspace: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get workspace: {str(e)}"
        )

@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    update_data: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    workspace = await workspace_service.update_workspace(
        db=db, 
        workspace_id=workspace_id, 
        user_id=current_user["user"].id, 
        update_data=update_data
)

    return workspace

@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):

        await workspace_service.delete_workspace(
            db=db, 
            workspace_id=workspace_id, 
            user_id=current_user["user"].id
            )

        return None
    
@router.get("/{workspace_id}/status", response_model=WorkspaceStatusResponse)
async def get_workspace_status(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
    ):

    workspace_status = await workspace_service.get_workspace_status(
        db=db, 
        workspace_id=workspace_id, 
        user_id=current_user["user"].id
    )

    return workspace_status

@router.post("/{workspace_id/propose}", response_model=ProposeChangesResponse)
async def propose_changes(
    workspace_id: str,
    propose_data: ProposeChangesRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
    ):

    github_token = current_user.get("github_token")

    proposed_changes = await workspace_service.propose_changes(
        db=db, 
        workspace_id=workspace_id,
        user_id=current_user["user"].id,
        commit_message=propose_data.commit_message,
        pr_title=propose_data.pr_title,
        pr_description=propose_data.pr_description,
        access_token=github_token
    )

    return proposed_changes

@router.get("/{workspace_id}/build/status", response_model=BuildStatusResponse)
async def get_build_status(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    build_status = await workspace_service.get_build_status(
        db=db,
        workspace_id=workspace_id,
        user_id=current_user["user"].id
    )
    return build_status

@router.post("/{workspace_id}/build/start")
async def start_build(
    workspace_id: str,
    build_data: StartBuildRequest = StartBuildRequest(),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    result = await workspace_service.start_manual_build(
        db=db,
        workspace_id=workspace_id,
        user_id=str(current_user.id),
        pfs=build_data.pfs
    )
    return result

@router.post("/{workspace_id}/build/cancel")
async def cancel_build(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    result = await workspace_service.cancel_build(
        db=db,
        workspace_id=workspace_id,
        user_id=str(current_user.id)
    )
    return result