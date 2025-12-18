import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from app.db.database import Base


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

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(50), nullable=False)
    description = Column(String(500), nullable=True)
    pfs = Column(JSON, nullable=True)
    user_id = Column(String(50), ForeignKey("users.id"), nullable=False)
    fork_repo_owner = Column(String(50), nullable=False)
    fork_repo_name = Column(String(50), nullable=False)
    branch_name = Column(String(50), nullable=False)
    workspace_path = Column(String(500), nullable=False)
    pull_request_number = Column(String, nullable=True)
    pull_request_status_last_updated_at = Column(DateTime, nullable=True)
    pull_request_status = Column(SQLAlchemyEnum(PullRequestStatus), nullable=True)
    status = Column(SQLAlchemyEnum(WorkspaceStatus), default=WorkspaceStatus.ACTIVE, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(), onupdate=datetime.now(), nullable=False)
    archived_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="workspaces")

    @property
    def deletion_at(self):
        """Calculate deletion date as 1 month after archived_at"""
        if self.archived_at:
            from dateutil.relativedelta import relativedelta

            return self.archived_at + relativedelta(months=1)
        return None

    def __repr__(self):
        return f"<Workspace id={self.id} title={self.title} status={self.status}>"
