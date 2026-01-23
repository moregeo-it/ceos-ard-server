from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.config import settings


class WorkspaceError(BaseModel):
    message: str
    code: int


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class GitStatusFile(BaseModel):
    path: str
    status: str


class GitStatus(BaseModel):
    branch: str
    is_clean: bool
    ahead_commits: int
    behind_commits: int
    modified_files: list[GitStatusFile]
    untracked_files: list[str]


class WorkspaceCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100, description="Workspace title")
    pfs: list[str] | None = Field(None, max_length=10, description="PFS to preview")
    description: str | None = Field(None, max_length=1000, description="Workspace description")


class WorkspaceUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=50, description="New workspace title")
    description: str | None = Field(None, max_length=1000, description="New workspace description (send null to clear)")
    pfs: list[str] | None = Field(None, max_length=10, description="PFS to update (send null to clear)")
    status: WorkspaceStatus | None = Field(None, description="New workspace status")


class WorkspaceResponse(BaseModel):
    id: str
    title: str
    user_id: str
    pfs: list[str] | None
    description: str | None
    fork_repo_owner: str
    fork_repo_name: str
    branch_name: str
    status: WorkspaceStatus
    pull_request_number: str | None
    pull_request_status: str | None
    pull_request_status_last_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    deletion_at: datetime | None  # Computed from archived_at + 1 month

    class ConfigDict:
        from_attributes = True


class ProposeChangesRequest(BaseModel):
    pr_title: str = Field(..., min_length=1, max_length=100, description="Pull request title")
    pr_description: str = Field(..., min_length=1, max_length=1000, description="Pull request description")


class ProposeChangesResponse(BaseModel):
    commit_sha: str | None
    pull_request: dict


class CreateFileRequest(BaseModel):
    name: str
    path: str
    type: str


class FilePatchOperation(str, Enum):
    RENAME = "rename"
    REVERT = "revert"


class FilePatchRequest(BaseModel):
    target: str | None = Field(None, min_length=1, max_length=100)
    operation: FilePatchOperation

    @model_validator(mode="after")
    def validate_rename(self):
        if self.operation == FilePatchOperation.RENAME and not self.target:
            raise ValueError("New name is required for rename operation")
        return self


class CreatePFSRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=10, description="PFS ID")
    title: str = Field(..., min_length=1, max_length=100, description="PFS title")
    version: str = Field(default=settings.PFS_DEFAULT_VERSION, description="PFS version")
    applies_to: str | None = Field(None, description="Description of the PFS")
    base_pfs: str | None = Field(None, description="Base PFS ID")
    type: str | None = Field(None, description="PFS type")
    introduction: list[str] | None = Field(default=settings.PFS_DEFAULT_INTRODUCTION.copy(), description="PFS introduction")

    class ConfigDict:
        use_enum_values = True


class PFSResponse(BaseModel):
    id: str
    name: str

    class ConfigDict:
        from_attributes = True


class FileListResponse(BaseModel):
    status: str | None
    name: str
    is_directory: bool
    path: str


class FileContextResponse(FileListResponse):
    usage: list[str]


class FileSearchResponse(BaseModel):
    name: str
    type: str
    path: str
    line: int | None = None
    column: int | None = None
    excerpt: str | None = None


class ChangedFilesResponse(BaseModel):
    path: str
    status: str | None = None
