from sqlalchemy import Column, String, DateTime, Enum as SQLAlchemyEnum

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