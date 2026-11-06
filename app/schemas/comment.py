from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.comment import ThreadStatus

# Markdown; long enough for a detailed review, short enough to keep threads readable
CommentBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class CommentAuthor(BaseModel):
    user_id: str | None = Field(description="The author's account in the editor; null once the account is deleted")
    github_id: str = Field(description="The author's GitHub account id, which never changes")
    username: str = Field(description="The author's GitHub username at the time")


class CommentResponse(BaseModel):
    id: str
    author: CommentAuthor
    body: str | None = Field(description="Markdown; null for a deleted first comment of a thread with replies")
    deleted: bool
    created_at: datetime
    edited_at: datetime | None = None


class ThreadResponse(BaseModel):
    id: str
    workspace_id: str
    path: str
    previous_paths: list[str]
    status: ThreadStatus
    outdated: bool = Field(description="The file no longer exists in the workspace")
    created_by: CommentAuthor
    created_at: datetime
    updated_at: datetime
    resolved_by: CommentAuthor | None = None
    resolved_at: datetime | None = None
    comments: list[CommentResponse]


class ThreadCreateRequest(BaseModel):
    path: str = Field(..., min_length=1, description="The file the thread is about")
    body: CommentBody


class ThreadUpdateRequest(BaseModel):
    status: ThreadStatus


class CommentRequest(BaseModel):
    body: CommentBody
