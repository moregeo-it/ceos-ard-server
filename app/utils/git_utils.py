from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import git
from fastapi import HTTPException, status

from app.schemas.workspace import Commit


def get_repo(path: Path | str) -> git.Repo:
    try:
        return git.Repo(path)
    except git.InvalidGitRepositoryError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Git repository is corrupted") from e


def get_file_info(repo: git.Repo, path: Path) -> dict[str, str] | None:
    try:
        path = path.resolve()
        workspace_path = Path(repo.working_dir)
        git_status = repo.git.status(path.parent, porcelain=True)
        for line in git_status.splitlines():
            info = extract_fileinfo(line)
            line_path = (workspace_path / info["path"]).resolve()
            if line_path == path:
                return info

        return None
    except git.GitCommandError:
        return None


def get_file_status(repo: git.Repo, path: Path) -> str | None:
    file = get_file_info(repo, path)
    if file:
        return file.get("status")
    return None


def extract_status(line: str) -> str:
    code = line[:2].strip()

    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}

    for marker, value in status_map.items():
        if marker in code:
            return value
    return None


def extract_fileinfo(line: str) -> str:
    file_path = line[3:].strip()
    file_status = extract_status(line)

    if file_status == "renamed":
        parts = file_path.split("->", maxsplit=1)
        if len(parts) == 2:
            old_path = parts[0].strip()
            new_path = parts[1].strip()
            return {"path": new_path, "status": file_status, "source": old_path}

    elif file_status:
        return {"path": file_path, "status": file_status}

    return None


def get_repo_changes(repo: git.Repo) -> list[dict[str, str]]:
    changed_files = []
    try:
        git_status = repo.git.status(porcelain=True)

        for line in git_status.splitlines():
            file_info = extract_fileinfo(line)
            if file_info:
                changed_files.append(file_info)

        return changed_files
    except git.GitCommandError:
        return changed_files


def format_commit(commit: list[dict[str, str]] | git.Commit) -> Commit:
    if isinstance(commit, git.Commit):
        return {
            "sha": commit.hexsha,
            "message": commit.message,
            "timestamp": commit.committed_datetime.isoformat(),
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
