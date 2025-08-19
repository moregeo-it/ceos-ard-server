import os
import re
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


def sanitize_path(path: str) -> str:
    normalized_path = os.path.normpath(path)

    normalized_path = normalized_path.lstrip("/")
    if normalized_path.startswith(".."):
        normalized_path = normalized_path.replace("..", "")

    normalized_path = normalized_path.replace("\\", "/")

    return normalized_path
