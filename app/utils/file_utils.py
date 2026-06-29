from pathlib import Path

import pygit2
from fastapi import HTTPException, status

from app.utils.git_utils import get_file_status, get_repo
from app.utils.validation import normalize_workspace_path


def create_file(workspace_path: Path, name: str, target_path: Path, content: bytes = None):
    repo = get_repo(workspace_path)
    try:
        if content is not None:
            target_path.write_bytes(content)
        else:
            target_path.touch()

        # Stage the file using pygit2
        relative_path = str(target_path.relative_to(workspace_path)).replace("\\", "/")
        repo.index.add(relative_path)
        repo.index.write()

        return {
            "name": name,
            "is_directory": False,
            "status": get_file_status(repo, target_path),
            "path": normalize_workspace_path(target_path, workspace_path),
        }
    except pygit2.GitError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The file has been created, but it failed to be added to the repository"
        ) from e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create file: {str(e)}") from e


def create_folder(workspace_path: Path, name: str, target_path: Path):
    repo = get_repo(workspace_path)
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        return {
            "name": name,
            "is_directory": True,
            "status": get_file_status(repo, target_path),
            "path": normalize_workspace_path(target_path, workspace_path),
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create folder: {str(e)}") from e
