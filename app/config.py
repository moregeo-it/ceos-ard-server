from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET")

    GITHUB_CLIENT_RESPONSE_TYPE: str = os.getenv("GITHUB_CLIENT_RESPONSE_TYPE", "code")
    GITHUB_API_BASE_URL: str = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com")
    GITHUB_CLIENT_SCOPE: str = os.getenv("GITHUB_CLIENT_SCOPE", "user:email read:org repo repo:status")
    GITHUB_TOKEN_URL: str = os.getenv("GITHUB_TOKEN_URL", "https://github.com/login/oauth/access_token")
    GITHUB_AUTHORIZE_URL: str = os.getenv("GITHUB_AUTHORIZE_URL", "https://github.com/login/oauth/authorize")

    GOOGLE_CLIENT_RESPONSE_TYPE: str = os.getenv("GOOGLE_CLIENT_RESPONSE_TYPE", "code")
    GOOGLE_CLIENT_SCOPE: str = os.getenv("GOOGLE_CLIENT_SCOPE", "openid email profile")
    GOOGLE_API_BASE_URL: str = os.getenv("GOOGLE_API_BASE_URL", "https://www.googleapis.com")
    GOOGLE_DISCOVERY_URL: str = os.getenv("GOOGLE_DISCOVERY_URL", "https://accounts.google.com/.well-known/openid-configuration")

    SECRET_KEY: str = os.getenv("SECRET_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    CALLBACK_BASE_URI: str = os.getenv("CALLBACK_BASE_URI")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    LOGOUT_REDIRECT: str = os.getenv("LOGOUT_REDIRECT")
    AUTH_SUCCESS_REDIRECT: str = os.getenv("AUTH_SUCCESS_REDIRECT")

settings = Settings()