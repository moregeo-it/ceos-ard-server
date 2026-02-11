import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Scopes (GitHub / Google):
# General:
#   --- / openid: Standard scope for OpenID Connect to get user identity.
#   --- / profile: to be confirmed, maybe needed for basic user information.
# Editor:
#   public_repo / ---: Allows to fork repositories for the editor under the org/user account so that PRs can be submitted.
#   repo:status / ---: Get the status of GitHub PRs and checks to update the status of the editor workspaces.
#   workflow / ---: To be able to push changes to branches that have GitHub Actions workflows defined.
# Assessor:
#   user:email / email: Stored for the assessor to notify about new assessments and results.
#   read:org / ---: Read permissions of users in the ceos-org GitHub organization to verify membership for access control to the Assessor.

CLIENT_URL = os.getenv("CLIENT_URL", "http://localhost:5173")
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")
DEFAULT_VERSION = "1.0-draft"
DEFAULT_INTRODUCTION = "what-are-ceos-ard-products,when-is-a-product-ceos-ard,difference-threshold-goal"


class Settings:
    CLIENT_URL: str = CLIENT_URL
    SERVER_URL: str = SERVER_URL

    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET")

    GITHUB_SERVICE_TOKEN: str = os.getenv("GITHUB_SERVICE_TOKEN")

    GITHUB_CLIENT_RESPONSE_TYPE: str = "code"
    GITHUB_API_BASE_URL: str = "https://api.github.com"
    GITHUB_CLIENT_SCOPE: str = "user:email read:org public_repo repo:status workflow"
    GITHUB_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
    GITHUB_AUTHORIZE_URL: str = "https://github.com/login/oauth/authorize"

    GOOGLE_CLIENT_RESPONSE_TYPE: str = "code"
    GOOGLE_CLIENT_SCOPE: str = "openid email profile"
    GOOGLE_API_BASE_URL: str = "https://www.googleapis.com"
    GOOGLE_DISCOVERY_URL: str = "https://accounts.google.com/.well-known/openid-configuration"

    CEOS_ARD_ORG: str = os.getenv("CEOS_ARD_ORG", "ceos-org")
    CEOS_ARD_REPO: str = os.getenv("CEOS_ARD_REPO", "ceos-ard")
    CEOS_ARD_BRANCH: str = os.getenv("CEOS_ARD_BRANCH", "main")

    SECRET_KEY: str = os.getenv("SECRET_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ceos_ard_server.db")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    CALLBACK_BASE_URI: str = os.getenv("CALLBACK_BASE_URI", f"{SERVER_URL}/auth/callback")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    CLIENT_URL: str = CLIENT_URL
    AUTH_SUCCESS_CLIENT_REDIRECT: str = os.getenv("AUTH_SUCCESS_CLIENT_REDIRECT", f"{CLIENT_URL}/auth/callback")

    WORKSPACES_ROOT: Path = Path(os.getenv("WORKSPACES_ROOT", "workspaces")).resolve()

    # PFS default version for new PFS documents
    PFS_DEFAULT_VERSION: str = os.getenv("PFS_DEFAULT_VERSION", DEFAULT_VERSION)
    # The sections that should be added to the introduction by default for new PFS documents
    # Separate sections with a comma (,)
    PFS_DEFAULT_INTRODUCTION: list[str] = [item.strip() for item in os.getenv("PFS_DEFAULT_INTRODUCTION", DEFAULT_INTRODUCTION).split(",")]

    # One or more CORS origins separated by commas
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", CLIENT_URL).split(",")


settings = Settings()
