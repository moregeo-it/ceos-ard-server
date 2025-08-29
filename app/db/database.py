from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

# Set pool_pre_ping to True to check the connection before each query
# Set pool_recycle to 7200 seconds (2 hours) to prevent stale connections
# due to inactivity, while also considering the low traffic volume of our app.
# This value is a trade-off between connection overhead and reliability.
engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_recycle=7200)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
