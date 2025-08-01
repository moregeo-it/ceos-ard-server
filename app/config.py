from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET")

    GITHUB_CLIENT_RESPONSE_TYPE: str = "code"
    GITHUB_API_BASE_URL: str =  "https://api.github.com"
    GITHUB_CLIENT_SCOPE: str = "user:email read:org repo repo:status"
    GITHUB_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
    GITHUB_AUTHORIZE_URL: str = "https://github.com/login/oauth/authorize"

    GOOGLE_CLIENT_RESPONSE_TYPE: str = "code"
    GOOGLE_CLIENT_SCOPE: str = "openid email profile"
    GOOGLE_API_BASE_URL: str =  "https://www.googleapis.com"
    GOOGLE_DISCOVERY_URL: str = "https://accounts.google.com/.well-known/openid-configuration"

    CEOS_ARD_OWNER: str = os.getenv("CEOS_ARD_OWNER", "ceos-org")
    CEOS_ARD_REPO: str = os.getenv("CEOS_ARD_REPO", "ceos-ard")
    CEOS_ARD_MAIN_BRANCH: str = os.getenv("CEOS_ARD_MAIN_BRANCH", "main")

    SECRET_KEY: str = os.getenv("SECRET_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    CALLBACK_BASE_URI: str = os.getenv("CALLBACK_BASE_URI")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    LOGOUT_REDIRECT: str = os.getenv("LOGOUT_REDIRECT")
    AUTH_SUCCESS_REDIRECT: str = os.getenv("AUTH_SUCCESS_REDIRECT")

    WORKSPACES_ROOT: str = os.getenv("WORKSPACES_ROOT", "../../tmp/workspaces")

    CORS_ORIGINS: list = [
        "http://localhost",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://localhost:5173",
    ]

settings = Settings()