from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import pygit2
from fastapi import HTTPException, status

from app.schemas.workspace import Commit


def get_repo(path: Path | str) -> pygit2.Repository:
    """Open a git repository at the given path."""
    try:
        return pygit2.Repository(str(path))
    except pygit2.GitError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Git repository is corrupted") from e


def get_file_status_from_flags(flags: int) -> str | None:
    """Convert pygit2 status flags to a status string."""
    if flags & (pygit2.GIT_STATUS_INDEX_NEW | pygit2.GIT_STATUS_WT_NEW):
        return "added"
    if flags & (pygit2.GIT_STATUS_INDEX_DELETED | pygit2.GIT_STATUS_WT_DELETED):
        return "deleted"
    if flags & (pygit2.GIT_STATUS_INDEX_RENAMED | pygit2.GIT_STATUS_WT_RENAMED):
        return "renamed"
    if flags & (pygit2.GIT_STATUS_INDEX_MODIFIED | pygit2.GIT_STATUS_WT_MODIFIED):
        return "modified"
    return None


def get_file_info(repo: pygit2.Repository, path: Path) -> dict[str, str] | None:
    """Get status info for a specific file in the repository."""
    try:
        path = path.resolve()
        workspace_path = Path(repo.workdir)
        relative_path = str(path.relative_to(workspace_path)).replace("\\", "/")

        status_dict = repo.status()

        # Check for the file in status
        if relative_path in status_dict:
            flags = status_dict[relative_path]
            file_status = get_file_status_from_flags(flags)
            if file_status:
                return {"path": relative_path, "status": file_status}

        # Check for renames by looking at the diff
        if repo.head_is_unborn:
            return None

        diff = repo.index.diff_to_tree(repo.head.peel().tree)
        diff.find_similar()  # Enable rename detection
        for delta in diff.deltas:
            if delta.status == pygit2.GIT_DELTA_RENAMED:
                if delta.new_file.path == relative_path:
                    return {"path": relative_path, "status": "renamed", "source": delta.old_file.path}

        return None
    except (pygit2.GitError, ValueError):
        return None


def get_file_status(repo: pygit2.Repository, path: Path) -> str | None:
    """Get the git status of a specific file."""
    file = get_file_info(repo, path)
    if file:
        return file.get("status")
    return None


def get_repo_changes(repo: pygit2.Repository) -> list[dict[str, str]]:
    """Get all changed files in the repository."""
    changed_files = []
    renamed_new_paths = set()
    renamed_old_paths = set()

    try:
        # First, detect renames using diff with rename detection
        if not repo.head_is_unborn:
            diff = repo.index.diff_to_tree(repo.head.peel().tree)
            diff.find_similar()  # Enable rename detection
            for delta in diff.deltas:
                if delta.status == pygit2.GIT_DELTA_RENAMED:
                    changed_files.append({
                        "path": delta.new_file.path,
                        "status": "renamed",
                        "source": delta.old_file.path,
                    })
                    renamed_new_paths.add(delta.new_file.path)
                    renamed_old_paths.add(delta.old_file.path)

        # Then get other status changes, excluding files that are part of renames
        status_dict = repo.status()
        for filepath, flags in status_dict.items():
            # Skip files that are part of a rename operation
            if filepath in renamed_new_paths or filepath in renamed_old_paths:
                continue
            file_status = get_file_status_from_flags(flags)
            if file_status:
                changed_files.append({"path": filepath, "status": file_status})

        return changed_files
    except pygit2.GitError:
        return changed_files


def format_commit(commit: list[dict[str, str]] | pygit2.Commit) -> Commit:
    """Format a commit object into a standardized dictionary."""
    if isinstance(commit, pygit2.Commit):
        from datetime import datetime, timezone

        commit_time = datetime.fromtimestamp(commit.commit_time, tz=timezone.utc)
        return {
            "sha": str(commit.id),
            "message": commit.message,
            "timestamp": commit_time.isoformat(),
        }
    else:
        return {
            "sha": commit["sha"],
            "url": commit["html_url"],
            "message": commit["commit"]["message"],
            "timestamp": commit["commit"]["committer"]["date"],
        }


def format_pr_response(pr_response: dict[str, Any], commits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": pr_response["title"],
        "state": pr_response["state"],
        "draft": pr_response["draft"],
        "url": pr_response["html_url"],
        "number": pr_response["number"],
        "description": pr_response["body"],
        "commits": [format_commit(commit) for commit in commits],
    }


def build_authenticated_url(remote_url: str, username: str, token: str) -> str:
    """
    Build a URL with embedded credentials for push operations.

    Args:
        remote_url: The original remote URL (https://github.com/owner/repo.git)
        username: GitHub username
        token: GitHub access token

    Returns:
        Authenticated URL (https://username:token@github.com/owner/repo.git)
    """
    parsed = urlparse(remote_url)

    if parsed.scheme != "https":
        raise ValueError("Only HTTPS URLs are supported for token authentication")

    # Rebuild URL with credentials embedded in netloc
    netloc = f"{username}:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"

    authenticated = parsed._replace(netloc=netloc)
    return urlunparse(authenticated)


def sanitize_git_error(error: Exception, username: str, token: str) -> str:
    """Remove sensitive credentials from error messages."""
    error_msg = str(error)
    if token:
        error_msg = error_msg.replace(token, "<ACCESS_TOKEN>")
    if username:
        error_msg = error_msg.replace(username, "<USERNAME>")
    return error_msg


class UserPassCredentials(pygit2.RemoteCallbacks):
    """Callbacks for pygit2 that provide username/password authentication."""

    def __init__(self, username: str, password: str):
        super().__init__()
        self._username = username
        self._password = password

    def credentials(self, url, username_from_url, allowed_types):
        if allowed_types & pygit2.GIT_CREDENTIAL_USERPASS_PLAINTEXT:
            return pygit2.UserPass(self._username, self._password)
        return None
