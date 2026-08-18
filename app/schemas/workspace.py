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


class SyncStatus(str, Enum):
    UP_TO_DATE = "up_to_date"
    UPDATED = "updated"  # fast-forwarded to remote
    MERGED = "merged"  # merge commit created (and best-effort pushed)
    CONFLICT = "conflict"  # merge aborted, repository fully restored
    DIRTY = "dirty"  # uncommitted local changes, merge skipped
    REMOTE_MISSING = "remote_missing"  # branch no longer exists on the fork
    REMOTE_RESTORED = "remote_restored"  # branch was missing on the fork and was pushed back


class SyncResult(BaseModel):
    status: SyncStatus
    ahead_commits: int = 0
    behind_commits: int = 0
    pulled_commits: int = 0
    conflicting_files: list[str] = []
    # True when the fork itself was missing on GitHub and had to be recreated before the sync
    repaired: bool = False


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


class ProposalRequest(BaseModel):
    draft: bool | None = Field(True, description="Whether the pull request is a draft")
    state: str | None = Field(None, description="State of the pull request (open, closed)")
    title: str = Field(..., min_length=1, max_length=200, description="Pull request title")
    description: str = Field(..., min_length=1, max_length=10000, description="Pull request description")


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, description="Commit message for the changes")


class Commit(BaseModel):
    sha: str
    message: str
    timestamp: datetime
    author: str


class CommitResult(Commit):
    # True when remote changes from the fork were merged into the workspace as part of the commit
    merged_remote: bool = False


class Proposal(BaseModel):
    number: int
    url: str
    title: str
    state: str
    draft: bool
    description: str
    # True when the fork this was opened from has been deleted. GitHub closed the pull request
    # and it can never be reopened, so proposing again opens a new one instead.
    detached: bool = False


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


class RequirementCategory(BaseModel):
    category: str
    requirements: list[str]


class CreatePFSRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=10, description="PFS ID")
    title: str = Field(..., min_length=1, max_length=100, description="PFS title")
    version: str = Field(default=settings.PFS_DEFAULT_VERSION, description="PFS version")
    applies_to: str | None = Field(None, description="Description of the PFS")
    base: str | None = Field(None, description="Base PFS ID")
    type: str | None = Field(None, description="PFS type")
    introduction: list[str] | None = Field(default=settings.PFS_DEFAULT_INTRODUCTION.copy(), description="PFS introduction")

    class ConfigDict:
        use_enum_values = True


class PfsType(BaseModel):
    id: str
    title: str | None = None


class PFSTypesResponse(BaseModel):
    pfsTypes: list[PfsType]


class FileResponse(BaseModel):
    status: str | None
    name: str
    is_directory: bool
    path: str


class FileContextResponse(FileResponse):
    usage: list[str] | None


class FileSearchResponse(BaseModel):
    name: str
    type: str
    path: str
    line: int | None = None
    column: int | None = None
    excerpt: str | None = None


class DiffFile(BaseModel):
    path: str
    status: str


class RenamedFile(BaseModel):
    path: str
    source: str
    status: str


class ListDiffsResponse(BaseModel):
    files: list[DiffFile | RenamedFile]
