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
    name='github',
    client_id=settings.GITHUB_CLIENT_ID,
    client_secret=settings.GITHUB_CLIENT_SECRET,
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={
        'scope': 'user:email read:org repo repo:status',
        'response_type': 'code',
    }
)

oauth.register(
    name='google',
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'email profile openid',
        'response_type': 'code',
    }
)