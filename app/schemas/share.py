from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.workspace_share import ShareMode, ShareStatus
from app.schemas.workspace import WorkspaceResponse


class ShareCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    github_usernames: list[str] = Field(..., alias="githubUsernames", min_length=1, description="GitHub usernames to grant access to")
    mode: ShareMode


class ShareUpdateRequest(BaseModel):
    mode: ShareMode


class WorkspaceShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(alias="workspaceId")
    share_link_id: str | None = Field(None, alias="shareLinkId")
    mode: ShareMode
    status: ShareStatus
    invitee_github_username: str = Field(alias="invitedGithubUsername")
    invitee_user_id: str | None = Field(None, alias="invitedUserId")
    invited_by_user_id: str = Field(alias="invitedBy")
    created_at: datetime = Field(alias="createdAt")
    accepted_at: datetime | None = Field(None, alias="acceptedAt")
    revoked_at: datetime | None = Field(None, alias="revokedAt")


class ListSharesResponse(BaseModel):
    shares: list[WorkspaceShareResponse]


class ShareLinkCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: ShareMode
    expires_at: datetime | None = Field(None, alias="expiresAt", description="Optional expiry. Omit or set null for a link that does not expire.")


class ShareLinkUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: ShareMode | None = None
    is_active: bool | None = Field(None, alias="isActive")
    expires_at: datetime | None = Field(None, alias="expiresAt")


class WorkspaceShareLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(alias="workspaceId")
    mode: ShareMode
    is_active: bool = Field(alias="isActive")
    url: str = Field(description="The full shareable URL ({clientUrl}/share/{token})")
    created_by_user_id: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime | None = Field(None, alias="expiresAt")
    revoked_at: datetime | None = Field(None, alias="revokedAt")


class ListShareLinksResponse(BaseModel):
    shareLinks: list[WorkspaceShareLinkResponse]


class ShareLinkPreview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workspace_title: str = Field(alias="workspaceTitle")
    owner_display_name: str = Field(alias="ownerDisplayName")
    mode: ShareMode
    is_active: bool = Field(alias="isActive")


class RedeemShareLinkResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    share: WorkspaceShareResponse | None
    workspace: WorkspaceResponse
