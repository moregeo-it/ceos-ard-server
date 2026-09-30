from datetime import UTC, datetime

from fastapi import HTTPException, Request, Response, status
from starlette.requests import HTTPConnection

from app.config import settings
from app.utils.request_context import CLIENT_ID_HEADER


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


def lacks_client_id(request: Request) -> bool:
    """Cross-site request protection for the session cookie: another page, even on a sibling host of the same
    site, can make the browser send the cookie, but can't add a custom header without a CORS preflight, which
    only CORS_ORIGINS pass. A bearer header is not sent automatically, so those requests need no client id."""
    return not request.headers.get(CLIENT_ID_HEADER) and not bearer_token(request)


async def require_client_id(request: Request) -> None:
    """The protection POST, PUT, PATCH and DELETE get in app/main.py, for GET routes that change data."""
    if lacks_client_id(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"The {CLIENT_ID_HEADER} header is required")
