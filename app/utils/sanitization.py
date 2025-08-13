import os
import re
from typing import Any

from fastapi import HTTPException, status


def sanitize_string(value: str, max_length: int = 100) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Value must be a string")

    if len(value) > max_length:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Value too long (max {max_length} characters)")

    pattern = r"^[a-zA-Z0-9._-]+$"

    if not re.match(pattern, value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Value contains invalid characters. Only alphanumeric, dots, hyphens, and underscores are allowed",
        )

    return value.strip()


def sanitize_github_params(params: dict[str, Any]) -> dict[str, str]:
    sanitized = {}

    param_rules = {
        "owner": {"max_length": 39, "required": False},
        "repo": {"max_length": 100, "required": False},
        "branch": {"max_length": 250, "required": False},
    }

    for key, value in params.items():
        if key in param_rules and value is not None:
            rules = param_rules[key]
            sanitized[key] = sanitize_string(str(value), rules["max_length"])
        elif value is not None:
            sanitized[key] = sanitize_string(str(value))

    return sanitized


def sanitize_query_params(query_params: dict[str, Any]) -> dict[str, Any]:
    cleaned_params = {key: value for key, value in query_params.items() if value is not None and value != ""}

    return sanitize_github_params(cleaned_params)


def sanitize_filename(filename: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", filename)

    if sanitized.startswith("."):
        sanitized = sanitized[1:]

    return sanitized


def sanitize_path(path: str) -> str:
    normalized_path = os.path.normpath(path)

    normalized_path = normalized_path.lstrip("/")
    if normalized_path.startswith(".."):
        normalized_path = normalized_path.replace("..", "")

    normalized_path = normalized_path.replace("\\", "/")

    return normalized_path
