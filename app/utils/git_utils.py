from pathlib import Path
from typing import Any

import git


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
    except git.exc.GitCommandError:
        return None


def get_file_status(repo: git.Repo, path: Path) -> str | None:
    file = get_file_info(repo, path)
    if file:
        return file.get("status")
    return None


def extract_status(line: str) -> str:
    code = line[:2].strip()

    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}

    for marker, status in status_map.items():
        if marker in code:
            return status
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


def get_repo_changes(workspace_path: str) -> list[dict[str, str]]:
    changed_files = []
    try:
        repo = git.Repo(workspace_path)

        git_status = repo.git.status(porcelain=True)

        for line in git_status.splitlines():
            file_info = extract_fileinfo(line)
            if file_info:
                changed_files.append(file_info)

        return changed_files
    except git.exc.GitCommandError:
        return changed_files


def format_pr_response(pr_response: dict[str, Any], commits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": pr_response["title"],
        "state": pr_response["state"],
        "draft": pr_response["draft"],
        "url": pr_response["html_url"],
        "number": pr_response["number"],
        "description": pr_response["body"],
        "commits": [
            {
                "sha": commit["sha"],
                "url": commit["html_url"],
                "message": commit["commit"]["message"],
                "timestamp": commit["commit"]["committer"]["date"],
            }
            for commit in commits
        ],
    }
