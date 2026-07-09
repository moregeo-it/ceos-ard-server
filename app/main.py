import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, collab, core, file, preview, share, workspace
from app.config import settings
from app.db.database import Base, engine
from app.utils.cli_utils import fastapi_run_checks, load_project_info

logging.basicConfig(level=logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG)
logger = logging.getLogger(__name__)

title, version = load_project_info()

logger.info(f"Starting {title} version {version} in {settings.ENVIRONMENT} environment")

# Collaborative editing keeps its authority (per-file version + update log) in this process's
# memory, so real-time sync only works when all collaborators hit the same process. This is fine
# for the current single-uvicorn deployment; running multiple workers/instances would split
# collaborators into separate authorities until this is moved to a shared store.
logger.warning(
    "Collaborative editing uses an in-memory authority - deploy as a SINGLE process (no uvicorn "
    "--workers > 1 / multiple instances without a shared store)."
)

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

app.include_router(collab.router)
app.include_router(core.router)
app.include_router(file.router)
app.include_router(preview.router)
app.include_router(share.router)
app.include_router(workspace.router)
