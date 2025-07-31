from sqlalchemy import Column, String, DateTime, Enum as SQLAlchemyEnum
from sqlalchemy.orm import relationship

from enum import Enum

from app.db.database import Base

import uuid

class IdentityProvider(str, Enum):
    github = "github"
    google = "google"

class User(Base):
    __tablename__ = 'users'

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    external_id = Column(String, unique=True, index=True, nullable=False)
    identity_provider = Column(SQLAlchemyEnum(IdentityProvider), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    workspaces = relationship("GitWorkspace", back_populates="user")

    def __repr__(self):
        return f"<User id={self.id} username={self.username} email={self.email} provider={self.identity_provider} external_id={self.external_id}>"