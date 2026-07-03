import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Column, ForeignKey, String, UniqueConstraint, Boolean, JSON
from sqlalchemy.orm import relationship
from sqlalchemy import Enum as SqlAlchemyEnum

from app.db.database import Base
from app.db.types import UTCDateTime

class ShareMode(str, Enum):
    EDIT = "edit"
    COMMENT = "comment"
    READONLY = "readonly"

class ShareStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"

class WorkspaceShare(Base):
    __tablename__ = "workspace_shares"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String, ForeignKey("git_workspaces.id"), nullable=False)
    share_link_id = Column(String, ForeignKey("workspace_share_links.id"), nullable=True, index=True)

    invitee_github_username = Column(String, nullable=False, index=True)
    invitee_user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    invited_by_user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    mode = Column(SqlAlchemyEnum(ShareMode), nullable=False)
    status = Column(SqlAlchemyEnum(ShareStatus), nullable=False, default=ShareStatus.PENDING)

    created_at = Column(UTCDateTime, nullable=False, default=datetime.now(UTC))
    updated_at = Column(UTCDateTime, nullable=False, default=datetime.now(UTC), onupdate=datetime.now(UTC))
    accepted_at = Column(UTCDateTime, nullable=True)
    revoked_at = Column(UTCDateTime, nullable=True)

    workspace = relationship("GitWorkspace", back_populates="shares")
    invitee_user = relationship("User", foreign_keys=[invitee_user_id])
    invited_by_user = relationship("User", foreign_keys=[invited_by_user_id])

    __table_args__ = (
        UniqueConstraint("workspace_id", "invitee_github_username", name="uq_share_workspace_gh"),
    )

    def __repr__(self):
        return f"<WorkspaceShare id={self.id} workspace_id={self.workspace_id} invitee_user_id={self.invitee_user_id} invited_by_user_id={self.invited_by_user_id} mode={self.mode} status={self.status}>"

class WorkspaceShareLink(Base):
    __tablename__ = "workspace_share_links"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String, ForeignKey("git_workspaces.id"), nullable=False)

    mode = Column(SqlAlchemyEnum(ShareMode), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(UTCDateTime, nullable=True)

    revoked_at = Column(UTCDateTime, nullable=True)
    created_at = Column(UTCDateTime, nullable=False, default=datetime.now(UTC))
    updated_at = Column(UTCDateTime, nullable=False, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    workspace = relationship("GitWorkspace", back_populates="share_links")

    def __repr__(self):
        return f"<WorkspaceShareLink id={self.id} workspace_id={self.workspace_id} mode={self.mode} is_active={self.is_active}>"
