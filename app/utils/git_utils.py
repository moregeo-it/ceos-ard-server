from pathlib import Path

import git


def get_file_status(repo: git.Repo, path: Path) -> str | None:
    try:
        parent_dir = Path(path).parent
        git_status = repo.git.status(parent_dir, porcelain=True)

        for line in git_status.splitlines():
            if str(path) in line:
                return extract_status(line)
        return None
    except git.exc.GitCommandError:
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
