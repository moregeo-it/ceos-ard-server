from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth
from app.models import user
from app.config import settings
from app.db.database import Base, engine

app = FastAPI(title="CEOS ARD Server", version="0.1.0")

Base.metadata.create_all(bind=engine)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.include_router(auth.router)