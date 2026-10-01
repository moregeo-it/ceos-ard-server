"""Client id of the current request, for the realtime echo filter.

The editor sends a per-page-load id as `X-Client-Id` on mutating requests and as `client_id` on the
socket. The middleware in app/main.py stores it here, `build_event` copies it into `actor_client_id`,
and the gateway withholds an event only from the socket with the same user and client id, stripping
the field before sending.
"""

import re
from contextvars import ContextVar, Token

CLIENT_ID_HEADER = "X-Client-Id"
CLIENT_ID_QUERY_PARAM = "client_id"

# Malformed ids are ignored, not rejected: the filter is an optimisation.
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_client_id: ContextVar[str | None] = ContextVar("client_id", default=None)


def validate_client_id(value: str | None) -> str | None:
    if value and _CLIENT_ID_PATTERN.fullmatch(value):
        return value
    return None


def set_client_id(value: str | None) -> Token:
    return _client_id.set(value)


def reset_client_id(token: Token) -> None:
    _client_id.reset(token)


def get_client_id() -> str | None:
    return _client_id.get()
