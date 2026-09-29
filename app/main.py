import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app import dependencies
from app.api import auth, collab, core, file, preview, share, workspace
from app.config import settings
from app.db.database import Base, engine
from app.utils.cli_utils import load_project_info, run_checks
from app.utils.request_context import CLIENT_ID_HEADER, reset_client_id, set_client_id, validate_client_id

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

# Holds the OAuth state between /auth/login and the callback; Lax, because GitHub redirects back cross-site
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, https_only=settings.SESSION_COOKIE_SECURE)

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def client_id_context(request: Request, call_next):
    # Cross-site request protection for the session cookie: another page can make the browser send the
    # cookie, but can't add a custom header without a CORS preflight, which only CORS_ORIGINS pass.
    # A bearer header is not sent automatically, so those requests need no client id.
    if request.method in _STATE_CHANGING_METHODS and not request.headers.get(CLIENT_ID_HEADER) and "authorization" not in request.headers:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": f"The {CLIENT_ID_HEADER} header is required"})

    # Realtime echo filter: remember which client sent the request (app/utils/request_context.py).
    token = set_client_id(validate_client_id(request.headers.get(CLIENT_ID_HEADER)))
    try:
        return await call_next(request)
    finally:
        reset_client_id(token)


# Added last so it is the outermost middleware: the 403 above still carries the CORS headers the editor needs to read it
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PATCH", "PUT"],
    allow_headers=["Authorization", CLIENT_ID_HEADER],
)


app.include_router(auth.router)

app.include_router(core.router)
app.include_router(collab.router)
app.include_router(file.router)
app.include_router(preview.router)
app.include_router(share.router)
app.include_router(workspace.router)
