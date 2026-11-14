import os
import sys
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
# Without -draft, which the CLI adds to documents that aren't stable
PFS_DEFAULT_VERSION = "1.0.0"
PFS_DEFAULT_TYPE = "Optical"
PFS_DEFAULT_INTRODUCTION = "what-are-ceos-ard-products,when-is-a-product-ceos-ard,difference-threshold-goal"


def _playwright_browsers_path() -> Path:
    if path := os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        return Path(path).resolve()
    # Playwright's defaults
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", Path.home())) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "ms-playwright"


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
    # offline_access: an expiring GitHub token with a refresh token, no extra consent prompt
    GITHUB_CLIENT_SCOPE: str = "user:email read:org public_repo repo:status workflow offline_access"
    GITHUB_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
    GITHUB_AUTHORIZE_URL: str = "https://github.com/login/oauth/authorize"

    GOOGLE_CLIENT_RESPONSE_TYPE: str = "code"
    GOOGLE_CLIENT_SCOPE: str = "openid email profile"
    GOOGLE_API_BASE_URL: str = "https://www.googleapis.com"
    GOOGLE_DISCOVERY_URL: str = "https://accounts.google.com/.well-known/openid-configuration"
    GOOGLE_REVOKE_URL: str = "https://oauth2.googleapis.com/revoke"

    CEOS_ARD_ORG: str = os.getenv("CEOS_ARD_ORG", "ceos-org")
    CEOS_ARD_REPO: str = os.getenv("CEOS_ARD_REPO", "ceos-ard")
    CEOS_ARD_BRANCH: str = os.getenv("CEOS_ARD_BRANCH", "main")

    # Workspace types users can choose from, separated by commas; the first one is the default
    WORKSPACE_TYPES: list[str] = [item.strip() for item in os.getenv("WORKSPACE_TYPES", "ceos-ard").split(",") if item.strip()]

    SECRET_KEY: str = os.getenv("SECRET_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ceos_ard_server.db")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    CALLBACK_BASE_URI: str = os.getenv("CALLBACK_BASE_URI", f"{SERVER_URL}/auth/callback")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    BUILD_CONCURRENCY: int = int(os.getenv("BUILD_CONCURRENCY", "3"))
    BUILD_TIMEOUT: int = int(os.getenv("BUILD_TIMEOUT", "60"))
    # Run builds in bubblewrap and a delegated cgroup (Linux only); required outside development
    BUILD_SANDBOX: bool = os.getenv("BUILD_SANDBOX", "true").lower() != "false"
    # Limits for all builds together and for each build, enforced by the cgroup
    BUILD_CPU_CORES: float = float(os.getenv("BUILD_CPU_CORES", "3"))
    BUILD_MEMORY_MAX: str = os.getenv("BUILD_MEMORY_MAX", "1536M")
    BUILD_MEMORY_MAX_PER_BUILD: str = os.getenv("BUILD_MEMORY_MAX_PER_BUILD", "700M")
    # What a build may write to disk; everything else it writes is in memory
    BUILD_OUTPUT_MAX: str = os.getenv("BUILD_OUTPUT_MAX", "512M")
    # One pixi environment per CLI version, installed on demand
    TOOLCHAINS_ROOT: Path = Path(os.getenv("TOOLCHAINS_ROOT", "toolchains")).resolve()
    TOOLCHAIN_INSTALL_TIMEOUT: int = int(os.getenv("TOOLCHAIN_INSTALL_TIMEOUT", "600"))
    # Chromium for PDFs, shared by all toolchains
    PLAYWRIGHT_BROWSERS_PATH: Path = _playwright_browsers_path()
    # Largest file that can be saved or uploaded
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "1"))

    SHARING_MODES_ENABLED: list[str] = os.getenv("SHARING_MODES_ENABLED", "readonly,comment").split(",")

    CLIENT_URL: str = CLIENT_URL
    AUTH_SUCCESS_CLIENT_REDIRECT: str = os.getenv("AUTH_SUCCESS_CLIENT_REDIRECT", f"{CLIENT_URL}/auth/callback")

    WORKSPACES_ROOT: Path = Path(os.getenv("WORKSPACES_ROOT", "workspaces")).resolve()

    # PFS default version for new PFS documents
    PFS_DEFAULT_VERSION: str = os.getenv("PFS_DEFAULT_VERSION", PFS_DEFAULT_VERSION)
    # The sections that should be added to the introduction by default for new PFS documents
    # Separate sections with a comma (,)
    PFS_DEFAULT_INTRODUCTION: list[str] = [item.strip() for item in os.getenv("PFS_DEFAULT_INTRODUCTION", PFS_DEFAULT_INTRODUCTION).split(",")]

    # PFS default type for new PFS documents
    PFS_DEFAULT_TYPE: str = os.getenv("PFS_DEFAULT_TYPE", PFS_DEFAULT_TYPE)

    # One or more CORS origins separated by commas. Exact origins only: requests carry the session cookie.
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", CLIENT_URL).split(",")

    # The browser session is an HttpOnly cookie on the API host holding the JWT. Set to "false" only for
    # local development over plain HTTP (Safari drops Secure cookies on http://localhost); this also drops
    # the __Host- prefix, which requires Secure.
    SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false"
    SESSION_COOKIE_NAME: str = "__Host-session" if SESSION_COOKIE_SECURE else "ceos_ard_session"
    # __Host-: a sibling host of the same site can't plant its own OAuth state (login CSRF)
    OAUTH_STATE_COOKIE_NAME: str = "__Host-oauth_state" if SESSION_COOKIE_SECURE else "ceos_ard_oauth_state"


settings = Settings()
