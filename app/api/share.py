import logging
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_share_service
from app.schemas.share import (
    CollaboratorCreateRequest,
    CollaboratorResponse,
    CollaboratorUpdateRequest,
    RedeemResponse,
    ShareCreateRequest,
    SharePreview,
    ShareResponse,
    ShareUpdateRequest,
)
from app.schemas.workspace import WorkspaceResponse
from app.services.auth_service import require_github_user
from app.services.share_service import ShareService
from app.utils.http_utils import internal_errors

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Sharing"])


@router.get(
    "/workspaces/{workspace_id}/collaborators",
    summary="List the collaborators of a workspace",
    response_model=list[CollaboratorResponse],
    status_code=status.HTTP_200_OK,
)
async def list_workspace_collaborators(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("list workspace collaborators", logger):
        return await share_service.list_collaborators(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)


@router.post(
    "/workspaces/{workspace_id}/collaborators",
    summary="Add collaborators by GitHub username",
    response_model=list[CollaboratorResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_workspace_collaborators(
    workspace_id: str,
    collaborator_data: CollaboratorCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("add workspace collaborators", logger):
        return await share_service.add_collaborators(db=db, workspace_id=workspace_id, user=current_user["user"], request=collaborator_data)


@router.patch(
    "/workspaces/{workspace_id}/collaborators/{collaborator_id}",
    summary="Change a collaborator's access mode",
    response_model=CollaboratorResponse,
    status_code=status.HTTP_200_OK,
)
async def update_workspace_collaborator(
    workspace_id: str,
    collaborator_id: str,
    collaborator_data: CollaboratorUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("update workspace collaborator", logger):
        return await share_service.update_collaborator(
            db=db, workspace_id=workspace_id, collaborator_id=collaborator_id, user_id=current_user["user"].id, request=collaborator_data
        )


@router.delete(
    "/workspaces/{workspace_id}/collaborators/{collaborator_id}",
    summary="Revoke a collaborator's access",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workspace_collaborator(
    workspace_id: str,
    collaborator_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("revoke workspace collaborator", logger):
        await share_service.revoke_collaborator(db=db, workspace_id=workspace_id, collaborator_id=collaborator_id, user_id=current_user["user"].id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/workspaces/{workspace_id}/shares",
    summary="List the shares of a workspace",
    response_model=list[ShareResponse],
    status_code=status.HTTP_200_OK,
)
async def list_workspace_shares(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("list workspace shares", logger):
        return await share_service.list_shares(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)


@router.post(
    "/workspaces/{workspace_id}/shares",
    summary="Create a share",
    response_model=ShareResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_share(
    workspace_id: str,
    share_data: ShareCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("create workspace share", logger):
        return await share_service.create_share(db=db, workspace_id=workspace_id, user=current_user["user"], request=share_data)


@router.patch(
    "/workspaces/{workspace_id}/shares/{share_id}",
    summary="Update a share",
    response_model=ShareResponse,
    status_code=status.HTTP_200_OK,
)
async def update_workspace_share(
    workspace_id: str,
    share_id: str,
    share_data: ShareUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("update workspace share", logger):
        return await share_service.update_share(
            db=db, workspace_id=workspace_id, share_id=share_id, user_id=current_user["user"].id, request=share_data
        )


@router.delete(
    "/workspaces/{workspace_id}/shares/{share_id}",
    summary="Delete a share",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_share(
    workspace_id: str,
    share_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("delete workspace share", logger):
        await share_service.delete_share(db=db, workspace_id=workspace_id, share_id=share_id, user_id=current_user["user"].id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/shares/{token}",
    summary="Preview a share",
    description="What a share leads to, for the share page before login. Public: no sensitive data.",
    response_model=SharePreview,
)
async def get_share_preview(token: str, db: Session = Depends(get_db), share_service: ShareService = Depends(get_share_service)):
    with internal_errors("preview share", logger):
        return await share_service.get_share_preview(db=db, token=token)


@router.post(
    "/shares/{token}/redeem",
    summary="Redeem a share",
    description="Grant the logged-in GitHub user the share's access to the workspace",
    response_model=RedeemResponse,
)
async def redeem_share(
    token: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    with internal_errors("redeem share", logger):
        collaborator, workspace = await share_service.redeem_share(db=db, token=token, user=current_user["user"])
        return RedeemResponse(
            collaborator=CollaboratorResponse.model_validate(collaborator) if collaborator else None,
            workspace=WorkspaceResponse.model_validate(workspace),
        )
