from authlib.integrations.starlette_client import OAuth
from starlette.config import Config

from app.config import settings

config_data = {
    "GITHUB_CLIENT_ID": settings.GITHUB_CLIENT_ID,
    "GITHUB_CLIENT_SECRET": settings.GITHUB_CLIENT_SECRET,
    "GOOGLE_CLIENT_ID": settings.GOOGLE_CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": settings.GOOGLE_CLIENT_SECRET,
}

config = Config(environ=config_data)

oauth = OAuth(config)

oauth.register(
    name="github",
    client_id=settings.GITHUB_CLIENT_ID,
    client_secret=settings.GITHUB_CLIENT_SECRET,
    access_token_url=settings.GITHUB_TOKEN_URL,
    api_base_url=settings.GITHUB_API_BASE_URL,
    authorize_url=settings.GITHUB_AUTHORIZE_URL,
    client_kwargs={
        "scope": settings.GITHUB_CLIENT_SCOPE,
        "response_type": settings.GITHUB_CLIENT_RESPONSE_TYPE,
    },
)

oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url=settings.GOOGLE_DISCOVERY_URL,
    client_kwargs={
        "scope": settings.GOOGLE_CLIENT_SCOPE,
        "response_type": settings.GOOGLE_CLIENT_RESPONSE_TYPE,
    },
    authorize_params={
        "access_type": "offline",  # Required to receive refresh tokens
        "prompt": "consent",  # Force consent screen to ensure refresh token every time
    },
)
