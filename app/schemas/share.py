from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.workspace_share import ShareMode, ShareStatus
from app.schemas.workspace import WorkspaceResponse


class ShareCreateRequest(BaseModel):
    github_usernames: list[str] = Field(..., min_length=1, max_length=50, description="GitHub usernames to grant access to")
    mode: ShareMode


class ShareUpdateRequest(BaseModel):
    mode: ShareMode


class WorkspaceShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    share_link_id: str | None = None
    mode: ShareMode
    status: ShareStatus
    invitee_github_username: str
    invitee_user_id: str | None = None
    invited_by_user_id: str
    created_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class ShareLinkCreateRequest(BaseModel):
    mode: ShareMode
    expires_at: datetime | None = Field(None, description="Optional expiry. Omit or set null for a link that does not expire.")


class ShareLinkUpdateRequest(BaseModel):
    mode: ShareMode | None = None
    expires_at: datetime | None = None


class WorkspaceShareLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    mode: ShareMode
    url: str = Field(description="The full shareable URL ({clientUrl}/share/{token})")
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime | None = None


class ShareLinkPreview(BaseModel):
    workspace_title: str
    owner_display_name: str
    mode: ShareMode


class RedeemResponse(BaseModel):
    share: WorkspaceShareResponse | None = Field(description="None for the workspace owner, who needs no share")
    workspace: WorkspaceResponse
