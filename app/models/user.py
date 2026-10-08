import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Column, String
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from app.db.database import Base
from app.db.types import EncryptedString, UTCDateTime


class IdentityProvider(str, Enum):
    github = "github"
    google = "google"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    external_id = Column(String, unique=True, index=True, nullable=False)
    identity_provider = Column(SQLAlchemyEnum(IdentityProvider), nullable=False)
    access_token = Column(EncryptedString, nullable=True)  # Provider's access token (stored server-side, encrypted)
    refresh_token = Column(EncryptedString, nullable=True)  # Provider's refresh token (stored server-side, encrypted)
    token_expiry = Column(UTCDateTime, nullable=True)  # Access token expiry
    created_at = Column(UTCDateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(UTCDateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
    last_seen_at = Column(UTCDateTime, nullable=True)  # Last authenticated request, kept to the hour

    workspaces = relationship("GitWorkspace", back_populates="user")

    def __repr__(self):
        return f"<User id={self.id} username={self.username} email={self.email} provider={self.identity_provider} external_id={self.external_id}>"
