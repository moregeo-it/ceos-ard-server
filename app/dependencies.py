from app.services.build_service import BuildService
from app.services.file_service import FileService
from app.services.git_service import GitService
from app.services.github_service import GitHubService
from app.services.preview_service import PreviewService
from app.services.token_refresh_service import TokenRefreshService
from app.services.workspace_service import WorkspaceService


def get_git_service() -> GitService:
    return GitService()


def get_build_service() -> BuildService:
    return BuildService()


def get_github_service() -> GitHubService:
    return GitHubService()


def get_workspace_service() -> WorkspaceService:
    return WorkspaceService()


def get_file_service() -> FileService:
    return FileService()


def get_preview_service() -> PreviewService:
    return PreviewService()


def get_token_refresh_service() -> TokenRefreshService:
    return TokenRefreshService()
