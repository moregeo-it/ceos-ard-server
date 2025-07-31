from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from enum import Enum
from datetime import datetime

import uuid

from app.db.database import Base

class WorkspaceStatus(Enum):
    ACTIVE = "active"
    ERROR = "error"
    BUILDING = "building"
    CREATING = "creating"
    UPDATING = "updating"
    DELETED = "deleted"
class GitWorkspace(Base):
    __tablename__ = "git_workspaces"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(50), nullable=False)
    default_pfs = Column(String(50), nullable=True)
    user_id = Column(String(25), ForeignKey("users.id"), nullable=False)
    upstream_repo_owner = Column(String(50), nullable=False)
    upstream_repo_name = Column(String(50), nullable=False)
    forked_repo_owner = Column(String(50), nullable=False)
    forked_repo_name = Column(String(50), nullable=False)
    fork_repo_clone_url = Column(String, nullable=False)
    branch_name = Column(String(50), nullable=False)
    upstream_branch_name = Column(String(50), nullable=False)
    workspace_path = Column(String(100), nullable=False)
    error_message = Column(Text, nullable=True)
    status = Column(SQLAlchemyEnum(WorkspaceStatus), default=WorkspaceStatus.CREATING, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.utcnow(), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.utcnow(), onupdate=datetime.utcnow(), nullable=False)
    last_build_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="workspaces")

    def __repr__(self):
        return f"<Workspace id={self.id} title={self.title} status={self.status}>"