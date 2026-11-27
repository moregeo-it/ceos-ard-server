from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from sqlalchemy import JSON, Column, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from app.config import settings
from app.db.database import Base
from app.db.types import UTCDateTime, new_id
from app.utils.branch_name import BRANCH_NAME_MAX_LENGTH
from app.workspace_types import Upstream, WorkspaceType, get_type


class WorkspaceStatus(Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class PullRequestStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    UNKNOWN = "unknown"
    APPROVED = "approved"


class GitWorkspace(Base):
    __tablename__ = "git_workspaces"

    id = Column(String, primary_key=True, default=new_id)
    title = Column(String(100), nullable=False)
    description = Column(String(1000), nullable=True)
    type = Column(String(50), nullable=False)
    upstream_owner = Column(String(100), nullable=False)
    upstream_repo = Column(String(100), nullable=False)
    upstream_branch = Column(String(255), nullable=False)
    # What the "build" preview builds, e.g. PFS ids or a document; see WorkspaceType.selection
    selection = Column(JSON, nullable=True)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=False, index=True)
    fork_repo_owner = Column(String(50), nullable=False)
    fork_repo_name = Column(String(50), nullable=False)
    # Fixed at creation: renaming the workspace must not move its branch
    branch_name = Column(String(BRANCH_NAME_MAX_LENGTH), nullable=False, unique=True, index=True)
    pull_request_number = Column(Integer, nullable=True)
    pull_request_status_last_updated_at = Column(UTCDateTime, nullable=True)
    pull_request_status = Column(SQLAlchemyEnum(PullRequestStatus), nullable=True)
    status = Column(SQLAlchemyEnum(WorkspaceStatus), default=WorkspaceStatus.ACTIVE, nullable=False)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(UTCDateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
    archived_at = Column(UTCDateTime, nullable=True)

    user = relationship("User", back_populates="workspaces")
    collaborators = relationship("WorkspaceCollaborator", back_populates="workspace", cascade="all, delete-orphan")
    shares = relationship("WorkspaceShare", back_populates="workspace", cascade="all, delete-orphan")

    @property
    def abs_path(self) -> Path:
        return settings.WORKSPACES_ROOT / self.id

    @property
    def workspace_type(self) -> "WorkspaceType":
        return get_type(self.type)

    @property
    def upstream(self) -> "Upstream":
        return Upstream(self.upstream_owner, self.upstream_repo, self.upstream_branch)

    def annotate_viewer(self, role: str, owner=None) -> "GitWorkspace":
        """Set the per-request response fields (not columns): the caller's role and the owner's names."""
        owner = owner if owner is not None else self.user
        self.viewer_role = role
        self.owner_username = owner.username if owner else None
        self.owner_full_name = owner.full_name if owner else None
        return self

    @property
    def deletion_at(self):
        """Calculate deletion date as 1 month after archived_at"""
        if self.archived_at:
            from dateutil.relativedelta import relativedelta

            return self.archived_at + relativedelta(months=1)
        return None

    def __repr__(self):
        return f"<Workspace id={self.id} title={self.title} status={self.status}>"
