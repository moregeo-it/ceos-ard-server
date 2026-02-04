import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, file, pfs, preview, workspace
from app.config import settings
from app.db.database import Base, engine
from app.utils.cli_utils import fastapi_run_checks, load_project_info

logging.basicConfig(level=logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG)
logger = logging.getLogger(__name__)

title, version = load_project_info()

logger.info(f"Starting {title} version {version} in {settings.ENVIRONMENT} environment")

app = FastAPI(title=title, version=version, lifespan=fastapi_run_checks)

Base.metadata.create_all(bind=engine)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
    allow_headers=["Authorization"],
)

app.include_router(auth.router)

app.include_router(pfs.router)
app.include_router(file.router)
app.include_router(preview.router)
app.include_router(workspace.router)
