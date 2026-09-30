from datetime import UTC, datetime

from fastapi import Response
from starlette.requests import HTTPConnection

from app.config import settings


def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    """Store the JWT as the browser session: HttpOnly, host-only, and never sent on cross-site requests."""
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        token,
        max_age=max(0, int((expires_at - datetime.now(UTC)).total_seconds())),
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/", secure=settings.SESSION_COOKIE_SECURE, httponly=True, samesite="strict")


def bearer_token(connection: HTTPConnection) -> str | None:
    """The JWT from an `Authorization: Bearer` header; None for no header, another scheme or an empty credential."""
    scheme, _, credentials = connection.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials.strip() or None


def request_token(connection: HTTPConnection) -> str | None:
    """The caller's JWT: a bearer header (scripts, tests, Swagger) wins over the session cookie."""
    return bearer_token(connection) or connection.cookies.get(settings.SESSION_COOKIE_NAME) or None
