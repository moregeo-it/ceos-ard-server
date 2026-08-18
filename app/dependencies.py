from app.services.build_service import BuildService
from app.services.file_service import FileService
from app.services.git_service import GitService
from app.services.github_service import GitHubService
from app.services.preview_service import PreviewService
from app.services.token_refresh_service import TokenRefreshService
from app.services.workspace_service import WorkspaceService

# One instance of each service, wired together once: the services are stateless (all locks
# live in app/utils/locks.py), and GitHubService's shared HTTP client needs a single owner —
# it is closed in main.py's lifespan.
git_service = GitService()
build_service = BuildService()
github_service = GitHubService()
workspace_service = WorkspaceService(git_service=git_service, build_service=build_service, github_service=github_service)
file_service = FileService(git_service=git_service, workspace_service=workspace_service)
preview_service = PreviewService(build_service=build_service, workspace_service=workspace_service)
token_refresh_service = TokenRefreshService()


def get_git_service() -> GitService:
    return git_service


def get_build_service() -> BuildService:
    return build_service


def get_github_service() -> GitHubService:
    return github_service


def get_workspace_service() -> WorkspaceService:
    return workspace_service


def get_file_service() -> FileService:
    return file_service


def get_preview_service() -> PreviewService:
    return preview_service


def get_token_refresh_service() -> TokenRefreshService:
    return token_refresh_service
