from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum

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
    modified_files: List[GitStatusFile]
    untracked_files: List[str]

class WorkspaceCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100, description="Workspace title")
    pfs: List[str] = Field(..., min_items=1, max_items=50, description="PFS to preview")
    description: Optional[str] = Field(..., min_length=1, max_length=1000, description="Workspace description")

class WorkspaceUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=50, description="New workspace title")
    description: Optional[str] = Field(None, min_length=1, max_length=1000, description="New workspace description")

class WorkspaceResponse(BaseModel):
    id: str
    title: str
    user_id: str
    pfs: List[str]
    description: Optional[str]
    upstream_repo_owner: str
    upstream_repo_name: str
    forked_repo_owner: str
    forked_repo_name: str
    fork_repo_clone_url: str
    branch_name: str
    upstream_branch_name: str
    workspace_path: str
    status: WorkspaceStatus
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_build_at: Optional[datetime]

    class Config:
        from_attributes = True

class WorkspaceStatusResponse(BaseModel):
    workspace_status: str
    error_message: Optional[str]
    git_status: Optional[GitStatus]

class ProposeChangesRequest(BaseModel):
    commit_message: str = Field(..., min_length=1, max_length=200, description="Commit message")
    pr_title: str = Field(..., min_length=1, max_length=100, description="Pull request title")
    pr_description: str = Field(..., min_length=1, max_length=1000, description="Pull request description")

class ProposeChangesResponse(BaseModel):
    commit_sha: Optional[str]
    pull_request: dict

class CreateFileRequest(BaseModel):
    name: str
    path: str
    type: str

class FileOperation(str, Enum):
    RENAME = "rename"
    REVERT = "revert"

class FileOperationRequest(BaseModel):
    new_name: Optional[str] = Field(None, min_length=1, max_length=100)
    operation: FileOperation

    @model_validator(mode='after')
    def validate_rename(self):
        if self.operation == FileOperation.RENAME and not self.new_name:
            raise ValueError("New name is required for rename operation")
        return self