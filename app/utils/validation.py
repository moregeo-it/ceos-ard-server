import re
from pathlib import Path

from fastapi import HTTPException, status

IGNORE_ROOT_PATHS = {"build", "templates", ".git", ".github"}


def validate_pathname(filename: str) -> str:
    if not isinstance(filename, str) and len(filename) > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name must be a non-empty string",
        )

    filename = filename.strip()

    if ".." in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name cannot contain consecutive dots",
        )

    if len(filename) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name exceeds the maximum allowed length of 100 characters",
        )
    elif len(filename) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name must be at least 2 characters long",
        )

    if not re.match(r"^\w[\w.-]*\w$", filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Name contains invalid characters. Only alphanumeric characters, dots, hyphens and "
                "underscores are allowed. At the beginning and end only alphanumeric characters are allowed."
            ),
        )

    return filename


def validate_workspace_path(
    path: str | Path, workspace_path: Path, exists: bool | None = None, type: str | None = None, is_preview: bool = False
) -> Path:
    if type not in (None, "file", "folder"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Type must be either 'file', 'folder' or None",
        )

    if not workspace_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
    elif not workspace_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace path is not a directory",
        )

    if isinstance(path, Path):
        path = str(path)

    if not isinstance(path, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be a string",
        )

    path = path.strip()
    # Remove leading slash as it's not actually meant as an absolute path
    if path.startswith("/"):
        path = path[1:]

    base_path = workspace_path / "build" if is_preview else workspace_path
    abs_path = (workspace_path / path).resolve()
    if not abs_path.is_relative_to(base_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path '{path}' is outside the workspace directory",
        )

    if len(str(abs_path)) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path length exceeds the maximum allowed length of 255 characters",
        )

    if is_preview:
        ignored_paths = IGNORE_ROOT_PATHS.copy()
        ignored_paths.discard("build")
    else:
        ignored_paths = IGNORE_ROOT_PATHS

    if ignore_file_path(abs_path, abs_path.relative_to(workspace_path), ignored_paths):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path '{path}' is not accessible",
        )

    path_exists = abs_path.exists()
    if exists and not path_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Path '{path}' not found in workspace",
        )
    elif exists is False and path_exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path '{path}' already exists in workspace",
        )
    elif type == "file" and exists is not False and path_exists and not abs_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path '{path}' is not a file",
        )
    elif type == "folder" and exists is not False and path_exists and not abs_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path '{path}' is not a directory",
        )

    return abs_path


def normalize_workspace_path(path: str | Path, workspace_path: Path, absolute: bool = True) -> str:
    if not isinstance(path, Path):
        path = Path(path)
    path = str(path.relative_to(workspace_path)).replace("\\", "/")
    if path == ".":
        path = ""
    if absolute and not path.startswith("/"):
        path = "/" + path
    elif not absolute and path.startswith("/"):
        path = path[1:]
    return path


def ignore_file_path(file: Path, relative_path: Path, ignored_paths: set[str]) -> bool:
    # Determine the root entry of the path to correctly apply ignore rules
    if relative_path == Path("."):
        root_entry = file.name
    else:
        root_entry = relative_path.parts[0]

    # Ignore files and directories under configured root paths (e.g. build, templates, etc.)
    if root_entry in ignored_paths:
        return True

    # Ignore hidden files
    if file.name.startswith("."):
        return True
    return False
