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
