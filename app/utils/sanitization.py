import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

_ALLOWED_SEGMENT_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")


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

    # Normalize path separators and strip leading/trailing slashes
    normalized = path.replace("\\", "/").strip("/")

    # Return workspace root for empty or root paths
    if not normalized:
        return workspace_path.resolve()

    # Check for absolute paths after stripping (fixes leading slash issue)
    if Path(normalized).is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Absolute paths are not allowed",
        )

    # Split into segments and validate
    segments = normalized.split("/")

    for seg in segments:
        # Reject empty segments (shouldn't happen after strip, but be defensive)
        if not seg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path contains empty segments",
            )

        # Reject path traversal attempts
        if seg == "..":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must not contain '..'",
            )

        # Validate allowed characters per segment
        if not _ALLOWED_SEGMENT_PATTERN.match(seg):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path may only contain alphanumeric characters, dots, hyphens and underscores",
            )

    # Reconstruct and resolve
    target_path = (workspace_path / normalized).resolve()

    # Final security check: ensure resolved path is within workspace
    try:
        target_path.relative_to(workspace_path.resolve())
        return target_path
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is outside the workspace directory",
        ) from e

def fix_path(filepath: str | Path) -> str:
    filepath = str(filepath).replace("\\", "/")
    if not filepath.startswith("/"):
        filepath = "/" + filepath
    return filepath
