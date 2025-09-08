from typing import Any

from pydantic import BaseModel

from app.config import settings


class ErrorResponse(BaseModel):
    code: int
    message: str


def create_error_detail(operation: str, exception: Exception = None) -> str:
    """
    Create error detail message based on environment.
    In development, include exception details for debugging.
    In production, return generic messages for security.
    """
    base_message = f"Failed to {operation}"

    if settings.ENVIRONMENT == "development" and exception:
        return f"{base_message}: {str(exception)}"

    return base_message


def create_error_response(code: int, operation: str, exception: Exception = None) -> dict[str, Any]:
    """
    Create consistent error response structure.
    """
    message = create_error_detail(operation, exception)
    return {"code": code, "message": message}
