import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, file, pfs, preview, workspace
from app.config import settings
from app.db.database import Base, engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CEOS-ARD Server", version="0.1.0")

Base.metadata.create_all(bind=engine)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
    allow_headers=["Authorization"],
)

app.include_router(auth.router)

app.include_router(pfs.router)
app.include_router(file.router)
app.include_router(preview.router)
app.include_router(workspace.router)


@app.get("/health")
async def health():
    return {"status": "healthy"}
