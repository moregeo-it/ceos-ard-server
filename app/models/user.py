from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from app.db.database import Base
from app.db.types import UTCDateTime, new_id


class IdentityProvider(str, Enum):
    github = "github"
    google = "google"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_id)
    full_name = Column(String, nullable=True)
    # Not unique: GitHub frees renamed usernames for others, and one email can sign in with GitHub and Google
    email = Column(String, nullable=False)
    username = Column(String, nullable=False)
    external_id = Column(String, nullable=False)
    identity_provider = Column(SQLAlchemyEnum(IdentityProvider), nullable=False)
    access_token = Column(String, nullable=True)  # Provider's access token (stored server-side)
    refresh_token = Column(String, nullable=True)  # Provider's refresh token (stored server-side)
    token_expiry = Column(UTCDateTime, nullable=True)  # Access token expiry
    created_at = Column(UTCDateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(UTCDateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
    last_seen_at = Column(UTCDateTime, nullable=True)  # Last authenticated request, kept to the hour
    session_version = Column(Integer, nullable=False, default=0, server_default="0")  # Bumped on logout: JWTs with another sv are refused

    workspaces = relationship("GitWorkspace", back_populates="user")

    # A user is the account at its provider
    __table_args__ = (UniqueConstraint("identity_provider", "external_id", name="uq_users_identity_provider_external_id"),)

    def __repr__(self):
        return f"<User id={self.id} username={self.username} email={self.email} provider={self.identity_provider} external_id={self.external_id}>"
