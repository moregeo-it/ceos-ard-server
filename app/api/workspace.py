import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_workspace_service
from app.schemas.error import create_error_detail
from app.schemas.workspace import (
    CreatePFSRequest,
    PFSResponse,
    ProposalRequest,
    ProposalResponse,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services.auth_service import require_github_user
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


@router.post("/", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED, description="Create a new workspace with cloned repository")
async def create_workspace(
    workspace_data: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    user_id = current_user["user"].id
    username = current_user["user"].username
    access_token = current_user["user"].access_token

    try:
        return await workspace_service.create_workspace(
            db=db, user_id=user_id, username=username, workspace_data=workspace_data, access_token=access_token
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("create workspace", e)) from e


@router.get(
    "/",
    summary="List all workspaces",
    status_code=status.HTTP_200_OK,
    response_model=list[WorkspaceResponse],
    description="Retrieve all workspaces for a authenticated user",
)
async def get_user_workspaces(
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.get_user_workspaces(db=db, user_id=current_user["user"].id, access_token=current_user["user"].access_token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workspaces: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("get workspaces", e)) from e


@router.get(
    "/{workspace_id}",
    summary="Get a workspace",
    response_model=WorkspaceResponse,
    description="Retrieve detailed information about a specific workspace",
)
async def get_user_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.get_workspace(
            db=db, workspace_id=workspace_id, user_id=current_user["user"].id, access_token=current_user["user"].access_token
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("get workspace", e)) from e


@router.patch("/{workspace_id}", summary="Update a workspace", response_model=WorkspaceResponse, description="Update a workspace information")
async def update_workspace(
    workspace_id: str,
    update_data: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.update_workspace(db=db, workspace_id=workspace_id, user_id=current_user["user"].id, update_data=update_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("update workspace", e)) from e


@router.delete(
    "/{workspace_id}",
    response_model=None,
    summary="Delete a workspace",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Delete a workspace and all associated data",
)
async def delete_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.delete_workspace(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting workspace: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("delete workspace", e)) from e


@router.get(
    "/{workspace_id}/proposal",
    summary="Get existing pull request proposal",
    response_model=ProposalResponse,
    status_code=status.HTTP_200_OK,
    description="Retrieve the existing pull request in the original repository that proposes changes made in the workspace",
)
async def get_proposal_changes(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        pull_request = await workspace_service.get_proposal_changes(
            db=db,
            workspace_id=workspace_id,
            user_id=current_user["user"].id,
            access_token=current_user["user"].access_token,
        )

        return Response(status_code=status.HTTP_204_NO_CONTENT) if pull_request is None else pull_request

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting proposal changes: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("get proposal changes", e)) from e


@router.put(
    "/{workspace_id}/proposal",
    response_model=ProposalResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or update a pull request to propose changes",
    description="Create or update a pull request in the original repository to propose changes made in the workspace",
)
async def propose_changes(
    workspace_id: str,
    propose_data: ProposalRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.propose_changes(
            db=db,
            workspace_id=workspace_id,
            propose_data=propose_data,
            user_id=current_user["user"].id,
            access_token=current_user["user"].access_token,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error proposing changes: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("propose changes", e)) from e


@router.get(
    "/{workspace_id}/pfs", response_model=list[PFSResponse], summary="List PFS types", description="List PFS types of a workspace", tags=["PFS"]
)
async def list_workspace_pfs_types(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[PFSResponse]:
    try:
        return await workspace_service.get_workspace_pfs_types(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing Workspace PFS types: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("list PFS types", e)) from e


@router.post("/{workspace_id}/pfs", response_model=PFSResponse, summary="Create a PFS", description="Create a PFS of a workspace", tags=["PFS"])
async def create_workspace_pfs(
    workspace_id: str,
    create_pfs_request: CreatePFSRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> PFSResponse:
    try:
        return await workspace_service.create_workspace_pfs(
            db=db, workspace_id=workspace_id, user_id=current_user["user"].id, create_pfs_request=create_pfs_request
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating PFS: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("create PFS", e)) from e
