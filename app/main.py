import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import dependencies
from app.api import auth, core, file, preview, workspace
from app.config import settings
from app.db.database import Base, engine
from app.utils.cli_utils import load_project_info, run_checks

logging.basicConfig(level=logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG)
logger = logging.getLogger(__name__)

title, version = load_project_info()

logger.info(f"Starting {title} version {version} in {settings.ENVIRONMENT} environment")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_checks()
    yield
    # Close the shared GitHub HTTP client so shutdown doesn't leak its connections
    await dependencies.github_service.aclose()


app = FastAPI(title=title, version=version, lifespan=lifespan)

Base.metadata.create_all(bind=engine)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
    allow_headers=["Authorization"],
)

app.include_router(auth.router)

app.include_router(core.router)
app.include_router(file.router)
app.include_router(preview.router)
app.include_router(workspace.router)
