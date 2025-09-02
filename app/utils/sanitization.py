import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status


def sanitize_string(value: str, max_length: int = 100) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Value must be a string")

    value = value.strip()

    if len(value) > max_length:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Value too long (max {max_length} characters)")

    pattern = r"^[a-zA-Z0-9._-]+$"

    if not re.match(pattern, value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Value contains invalid characters. Only alphanumeric, dots, hyphens, and underscores are allowed",
        )

    return value


def sanitize_github_params(params: dict[str, Any]) -> dict[str, str]:
    sanitized = {}

    param_rules = {
        "owner": {"max_length": 39, "required": False},
        "repo": {"max_length": 100, "required": False},
        "branch": {"max_length": 250, "required": False},
    }

    for key, value in params.items():
        if value is None:
            continue
        rules = param_rules.get(key, {})
        sanitized[key] = sanitize_string(str(value), **rules)

    return sanitized


def sanitize_filename(filename: str) -> str:
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    if filename.endswith(
        (
            ".",
            " ",
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename cannot end with a dot or whitespace",
        )

    if ".." in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename cannot contain consecutive dots",
        )

    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)

    if sanitized.startswith("."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename cannot start with a dot",
        )

    return sanitized


def sanitize_path(path: str, workspace_path: Path) -> Path:
    if not workspace_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    if not workspace_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace path is not a directory",
        )

    if not isinstance(path, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be a string",
        )

    if not path or path == "/":
        return workspace_path.resolve()

    sanitized_path = path.strip("/")

    allowed_patterns = r"^[a-zA-Z0-9/_\-\.]+$"

    if not re.match(allowed_patterns, path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path contains invalid characters or patterns",
        )

    if sanitized_path.startswith("/") or len(sanitized_path) > 1 and sanitized_path[1] == ":":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Absolute paths are not allowed",
        )

    target_path = (workspace_path / sanitized_path).resolve()
    workspace_path_resolved = workspace_path.resolve()
    try:
        target_path.relative_to(workspace_path_resolved)
        return target_path
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is outside the workspace directory",
        ) from e
