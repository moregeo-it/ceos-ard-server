from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# Checked by the workspace type, see WorkspaceType.output_name()
SelectionItem = Annotated[str, StringConstraints(min_length=1, max_length=500)]
# GitHub's repository names
RepositoryName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.\-]{1,100}$")]


class WorkspaceError(BaseModel):
    message: str
    code: int


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ViewerRole(str, Enum):
    OWNER = "owner"
    READONLY = "readonly"
    COMMENT = "comment"


class SyncStatus(str, Enum):
    UP_TO_DATE = "up_to_date"
    UPDATED = "updated"  # moved to the remote (a fast-forward, or reset to a branch rewritten on GitHub)
    MERGED = "merged"  # local commits replayed on top of the remote, no merge commit (best-effort pushed)
    CONFLICT = "conflict"  # merge aborted, repository fully restored
    DIRTY = "dirty"  # uncommitted local changes, merge skipped
    REMOTE_MISSING = "remote_missing"  # branch no longer exists on the fork
    REMOTE_RESTORED = "remote_restored"  # branch was missing on the fork and was pushed back


class UpstreamStatus(BaseModel):
    behind_commits: int = 0  # commits on the upstream repository the branch doesn't have
    conflicting_files: list[str] = []  # files merging them would conflict on, as the pull request shows


class SyncResult(BaseModel):
    status: SyncStatus
    ahead_commits: int = 0
    behind_commits: int = 0
    pulled_commits: int = 0
    conflicting_files: list[str] = []
    # True when the fork itself was missing on GitHub and had to be recreated before the sync
    repaired: bool = False
    # The branch compared with the upstream repository; None when unknown, or the proposal is merged or closed
    upstream: UpstreamStatus | None = None


class WorkspaceCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100, description="Workspace title")
    type: str | None = Field(None, description="Workspace type, the configured default if omitted")
    selection: list[SelectionItem] | None = Field(
        None, min_length=1, max_length=50, description="What the preview builds, for workspace types that select it on creation"
    )
    description: str | None = Field(None, max_length=1000, description="Workspace description")
    repository: RepositoryName | None = Field(None, description="The repository of the type's upstream owner, for types that choose one")
    branch: str | None = Field(None, min_length=1, max_length=255, description="Its branch to start from, the default branch if omitted")
    pull_request: int | None = Field(
        None,
        ge=1,
        description="An open pull request of the repository to continue instead of a branch: the workspace starts from its head "
        "and proposes changes to its branch",
    )


class WorkspaceUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=100, description="New workspace title")
    description: str | None = Field(None, max_length=1000, description="New workspace description (send null to clear)")
    selection: list[SelectionItem] | None = Field(None, min_length=1, max_length=50, description="What the preview builds")
    status: WorkspaceStatus | None = Field(None, description="New workspace status")

    @field_validator("title", "selection", "status")
    @classmethod
    def not_null(cls, value):
        """Omitting a field keeps it; only the description can be cleared with null."""
        if value is None:
            raise ValueError("cannot be null, omit it to keep the current value")
        return value


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    type: str
    upstream_owner: str
    upstream_repo: str
    upstream_branch: str
    user_id: str
    selection: list[str] | None
    description: str | None
    fork_repo_owner: str
    fork_repo_name: str
    branch_name: str
    status: WorkspaceStatus
    pull_request_number: int | None
    pull_request_status: str | None
    pull_request_status_last_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    deletion_at: datetime | None  # Computed from archived_at + 1 month
    viewer_role: ViewerRole = Field(description="The current authenticated user's effective role on this workspace")
    owner_username: str | None = Field(None, description="GitHub username of the workspace owner")
    owner_full_name: str | None = Field(None, description="Full name of the workspace owner")


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
    # True when remote changes from the fork were brought into the workspace as part of the commit
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


class Repository(BaseModel):
    name: str
    description: str | None
    default_branch: str


class PullRequest(BaseModel):
    number: int
    title: str
    author: str | None
    # Where its changes are, as owner:branch
    head: str


class SelectionOption(BaseModel):
    id: str
    title: str
    path: str | None = Field(None, description="The file or folder of the workspace that defines the option")


class SelectionOptionsResponse(BaseModel):
    options: list[SelectionOption]


class FileResponse(BaseModel):
    status: str | None
    name: str
    is_directory: bool
    path: str


class ActionResponse(FileResponse):
    open: str | None = Field(None, description="The file the editor opens, if any")


class FileUsage(BaseModel):
    label: str
    path: str


class FileContextResponse(FileResponse):
    usage: list[FileUsage] | None


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
