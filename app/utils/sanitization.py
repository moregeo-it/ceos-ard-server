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
    if not isinstance(filename, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename must be a string",
        )

    filename = filename.strip()

    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    if ".." in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename cannot contain consecutive dots",
        )

    allowed_pattern = r"^[\w][\w._-]+\w$"
    if not re.match(allowed_pattern, filename, flags=re.IGNORECASE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Filename contains invalid characters. Only alphanumeric characters, dots, hyphens and "
                "underscores are allowed. At the beginning and end only alphanumeric characters are allowed."
            ),
        )

    return filename


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

    normalized = path.replace("\\", "/")

    if Path(normalized).is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Absolute paths are not allowed",
        )

    # Remove leading/trailing slashes
    sanitized_path = normalized.strip("/")

    # Reject empty segments and '..' explicitly
    segments = [s for s in sanitized_path.split("/") if s != ""]
    if any(seg == ".." for seg in segments):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path must not contain '..'")
    if len(segments) == 0:
        return workspace_path.resolve()

    # Validate allowed characters per segment (dots allowed inside names)
    allowed_segment = re.compile(r"^[a-zA-Z0-9._-]+$")  # letters, digits, dot, underscore, hyphen
    for seg in segments:
        if not allowed_segment.match(seg):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path may only contain alphanumeric characters, dots, hyphens and underscores",
            )

    # Reconstruct and resolve
    joined = "/".join(segments)
    target_path = (workspace_path / joined).resolve()
    try:
        target_path.relative_to(workspace_path.resolve())
        return target_path
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is outside the workspace directory",
        ) from e
