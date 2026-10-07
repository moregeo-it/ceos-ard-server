import secrets
import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Column, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SqlAlchemyEnum
from sqlalchemy.orm import relationship

from app.config import settings
from app.db.database import Base
from app.db.types import UTCDateTime


class AccessMode(str, Enum):
    READONLY = "readonly"


class CollaboratorStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class WorkspaceCollaborator(Base):
    __tablename__ = "workspace_collaborators"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String, ForeignKey("git_workspaces.id"), nullable=False)
    share_id = Column(String, ForeignKey("workspace_shares.id"), nullable=True, index=True)

    invitee_github_id = Column(String, nullable=False, index=True)
    invitee_github_username = Column(String, nullable=False)
    invitee_user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    invited_by_user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    mode = Column(SqlAlchemyEnum(AccessMode), nullable=False)
    status = Column(SqlAlchemyEnum(CollaboratorStatus), nullable=False, default=CollaboratorStatus.PENDING)

    created_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    accepted_at = Column(UTCDateTime, nullable=True)
    revoked_at = Column(UTCDateTime, nullable=True)

    workspace = relationship("GitWorkspace", back_populates="collaborators")
    invitee_user = relationship("User", foreign_keys=[invitee_user_id])
    invited_by_user = relationship("User", foreign_keys=[invited_by_user_id])

    __table_args__ = (UniqueConstraint("workspace_id", "invitee_github_id", name="uq_collaborator_workspace_github_id"),)

    def accept(self, user_id: str, github_username: str, now: datetime) -> None:
        """Bind the collaborator to the invitee's account; one accepted earlier keeps its date."""
        self.invitee_user_id = user_id
        self.invitee_github_username = github_username
        self.status = CollaboratorStatus.ACCEPTED
        self.accepted_at = self.accepted_at or now

    def __repr__(self):
        return f"<WorkspaceCollaborator id={self.id} workspace_id={self.workspace_id} invitee_user_id={self.invitee_user_id} invited_by_user_id={self.invited_by_user_id} mode={self.mode} status={self.status}>"  # noqa: E501


class WorkspaceShare(Base):
    __tablename__ = "workspace_shares"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id = Column(String, ForeignKey("git_workspaces.id"), nullable=False)
    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    # Random rather than signed, so rotating SECRET_KEY doesn't break every share ever sent
    token = Column(String, nullable=False, unique=True, index=True, default=lambda: secrets.token_urlsafe(32))

    mode = Column(SqlAlchemyEnum(AccessMode), nullable=False)
    expires_at = Column(UTCDateTime, nullable=True)

    created_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    workspace = relationship("GitWorkspace", back_populates="shares")
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])

    @property
    def url(self) -> str:
        return f"{settings.CLIENT_URL}/share/{self.token}"

    def __repr__(self):
        return f"<WorkspaceShare id={self.id} workspace_id={self.workspace_id} mode={self.mode}>"
