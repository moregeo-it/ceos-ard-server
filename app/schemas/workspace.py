from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    ERROR = "error"
    BUILDING = "building"
    CREATING = "creating"
    UPDATING = "updating"
    DELETED = "deleted"


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
    pfs: list[str] | None = Field(None, min_items=1, max_items=10, description="PFS to preview")
    description: str | None = Field(..., min_length=1, max_length=1000, description="Workspace description")


class WorkspaceUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=50, description="New workspace title")
    description: str | None = Field(None, min_length=1, max_length=1000, description="New workspace description")


class WorkspaceResponse(BaseModel):
    id: str
    title: str
    user_id: str
    pfs: list[str] | None
    description: str | None
    upstream_repo_owner: str
    upstream_repo_name: str
    forked_repo_owner: str
    forked_repo_name: str
    fork_repo_clone_url: str
    branch_name: str
    upstream_branch_name: str
    workspace_path: str
    status: WorkspaceStatus
    pull_request_url: str | None
    pull_request_number: str | None
    pull_request_status: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    last_build_at: datetime | None

    class Config:
        from_attributes = True


class WorkspaceStatusResponse(BaseModel):
    workspace_status: str
    error_message: str | None
    git_status: GitStatus | None


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


class FileOperation(str, Enum):
    RENAME = "rename"
    REVERT = "revert"


class FileOperationRequest(BaseModel):
    new_name: str | None = Field(None, min_length=1, max_length=100)
    operation: FileOperation

    @model_validator(mode="after")
    def validate_rename(self):
        if self.operation == FileOperation.RENAME and not self.new_name:
            raise ValueError("New name is required for rename operation")
        return self


class CreateNewPFSType(str, Enum):
    OPTICAL = "optical"
    SAR = "sar"


class CreatePFSRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=10, description="PFS ID")
    title: str = Field(..., min_length=1, max_length=100, description="PFS title")
    version: str = Field(default="1.0", description="PFS version")
    applies_to: str = Field(..., description="Description of the PFS")
    base_pfs: str | None = Field(None, description="Base PFS ID")
    type: CreateNewPFSType = Field(..., description="PFS type")
    introduction: list[str] = Field(
        default=["what-are-ceos-ard-products", "when-is-a-product-ceos-ard", "difference-threshold-goal"], description="PFS introduction"
    )
