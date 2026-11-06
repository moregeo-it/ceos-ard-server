import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column, ForeignKey, String, Text
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from app.db.database import Base
from app.db.types import UTCDateTime


class ThreadStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class CommentThread(Base):
    """A discussion on a file of a workspace: a first comment and a flat list of replies.

    Threads outlive their workspace: deleting it only clears `workspace_id` (ON DELETE SET NULL),
    and `repository` and `path` still tell which building block the discussion was about.
    Authors are kept by GitHub id and username, so removed collaborators' comments stay.
    """

    __tablename__ = "comment_threads"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String, ForeignKey("git_workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    # The upstream repository the workspace is a fork of, e.g. "ceos-org/ceos-ard"
    repository = Column(String(201), nullable=False)
    # Path of the file in the workspace, as the file API names it, e.g. "/requirements/x.yaml"
    path = Column(String, nullable=False)
    # Earlier paths of the file, oldest first, from renames
    previous_paths = Column(JSON, nullable=False, default=list)
    status = Column(SQLAlchemyEnum(ThreadStatus), nullable=False, default=ThreadStatus.OPEN)

    created_by_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_github_id = Column(String, nullable=False)
    created_by_username = Column(String, nullable=False)
    resolved_by_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_by_github_id = Column(String, nullable=True)
    resolved_by_username = Column(String, nullable=True)
    resolved_at = Column(UTCDateTime, nullable=True)

    created_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    comments = relationship("Comment", back_populates="thread", cascade="all, delete-orphan", order_by="Comment.created_at")

    def __repr__(self):
        return f"<CommentThread id={self.id} workspace_id={self.workspace_id} path={self.path} status={self.status}>"


class Comment(Base):
    """A comment in a thread. Markdown. A deleted first comment of a thread with replies stays as a placeholder (`deleted_at`)."""

    __tablename__ = "comments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("comment_threads.id", ondelete="CASCADE"), nullable=False, index=True)

    author_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_github_id = Column(String, nullable=False)
    author_username = Column(String, nullable=False)
    body = Column(Text, nullable=True)

    created_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC))
    edited_at = Column(UTCDateTime, nullable=True)
    deleted_at = Column(UTCDateTime, nullable=True)

    thread = relationship("CommentThread", back_populates="comments")

    def __repr__(self):
        return f"<Comment id={self.id} thread_id={self.thread_id}>"
