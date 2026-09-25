import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_share_service
from app.schemas.error import create_error_detail
from app.schemas.share import (
    ShareCreateRequest,
    ShareLinkCreateRequest,
    ShareLinkUpdateRequest,
    ShareUpdateRequest,
    WorkspaceShareLinkResponse,
    WorkspaceShareResponse,
)
from app.schemas.workspace import WorkspaceResponse
from app.services.auth_service import get_optional_current_user, require_github_user
from app.services.share_service import ShareService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Sharing"])


@router.get(
    "/workspaces/{workspace_id}/shares",
    summary="List all shares for a workspace",
    response_model=list[WorkspaceShareResponse],
    status_code=status.HTTP_200_OK,
)
async def list_workspace_shares(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        return await share_service.list_shares(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing workspace shares: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("list workspace shares", e)) from e


@router.post(
    "/workspaces/{workspace_id}/shares",
    summary="Share a workspace with specific people",
    response_model=list[WorkspaceShareResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_shares(
    workspace_id: str,
    share_data: ShareCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        return await share_service.create_shares(db=db, workspace_id=workspace_id, user=current_user["user"], request=share_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating workspace shares: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("create workspace shares", e)) from e


@router.patch(
    "/workspaces/{workspace_id}/shares/{share_id}",
    summary="Change a collaborator's access mode",
    response_model=WorkspaceShareResponse,
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
    try:
        return await share_service.update_share(
            db=db, workspace_id=workspace_id, share_id=share_id, user_id=current_user["user"].id, request=share_data
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workspace share: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("update workspace share", e)) from e


@router.delete(
    "/workspaces/{workspace_id}/shares/{share_id}",
    summary="Revoke a collaborator's access",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workspace_share(
    workspace_id: str,
    share_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        await share_service.revoke_share(db=db, workspace_id=workspace_id, share_id=share_id, user_id=current_user["user"].id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking workspace share: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("revoke workspace share", e)) from e


@router.get(
    "/workspaces/{workspace_id}/share-links",
    summary="List share links for a workspace",
    response_model=list[WorkspaceShareLinkResponse],
    status_code=status.HTTP_200_OK,
)
async def list_workspace_share_links(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        return await share_service.list_share_links(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing workspace share links: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("list workspace share links", e)) from e


@router.post(
    "/workspaces/{workspace_id}/share-links",
    summary="Create a share link",
    response_model=WorkspaceShareLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_share_link(
    workspace_id: str,
    link_data: ShareLinkCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        return await share_service.create_share_link(db=db, workspace_id=workspace_id, user=current_user["user"], request=link_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating workspace share link: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("create workspace share link", e)) from e


@router.patch(
    "/workspaces/{workspace_id}/share-links/{link_id}",
    summary="Update a share link",
    response_model=WorkspaceShareLinkResponse,
    status_code=status.HTTP_200_OK,
)
async def update_workspace_share_link(
    workspace_id: str,
    link_id: str,
    link_data: ShareLinkUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        return await share_service.update_share_link(
            db=db, workspace_id=workspace_id, link_id=link_id, user_id=current_user["user"].id, request=link_data
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workspace share link: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("update workspace share link", e)) from e


@router.delete(
    "/workspaces/{workspace_id}/share-links/{link_id}",
    summary="Delete a share link",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_share_link(
    workspace_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        await share_service.delete_share_link(db=db, workspace_id=workspace_id, link_id=link_id, user_id=current_user["user"].id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting workspace share link: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("delete workspace share link", e)) from e


@router.post(
    "/share-links/{token}/redeem",
    summary="Redeem a share link",
    status_code=status.HTTP_200_OK,
)
async def redeem_share_link(
    token: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] | None = Depends(get_optional_current_user),
    share_service: ShareService = Depends(get_share_service),
):
    try:
        if not current_user:
            preview = await share_service.get_share_link_preview(db=db, token=token)
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=preview.model_dump(by_alias=True))

        if current_user["provider"] != "github":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace share links require GitHub authentication")
        share, workspace = await share_service.redeem_share_link(db=db, token=token, user=current_user["user"])

        return {
            "share": WorkspaceShareResponse.model_validate(share).model_dump(by_alias=True) if share else None,
            "workspace": WorkspaceResponse.model_validate(workspace).model_dump(by_alias=True),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error redeeming share link: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=create_error_detail("redeem share link", e)) from e
