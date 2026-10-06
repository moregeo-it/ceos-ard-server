from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.workspace_share import AccessMode, CollaboratorStatus
from app.schemas.workspace import WorkspaceResponse


class CollaboratorCreateRequest(BaseModel):
    github_usernames: list[str] = Field(..., min_length=1, max_length=50, description="GitHub usernames to grant access to")
    mode: AccessMode


class CollaboratorUpdateRequest(BaseModel):
    mode: AccessMode


class CollaboratorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    share_id: str | None = None
    mode: AccessMode
    status: CollaboratorStatus
    invitee_github_username: str
    invitee_user_id: str | None = None
    invited_by_user_id: str
    created_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class ShareCreateRequest(BaseModel):
    mode: AccessMode
    expires_at: datetime | None = Field(None, description="Optional expiry. Omit or set null for a share that does not expire.")


class ShareUpdateRequest(BaseModel):
    mode: AccessMode | None = None
    expires_at: datetime | None = None


class ShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    mode: AccessMode
    url: str = Field(description="The full shareable URL ({clientUrl}/share/{token})")
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime | None = None


class SharePreview(BaseModel):
    workspace_title: str
    owner_display_name: str
    mode: AccessMode


class RedeemResponse(BaseModel):
    collaborator: CollaboratorResponse | None = Field(description="None for the workspace owner, who needs no collaborator entry")
    workspace: WorkspaceResponse
