from datetime import UTC, datetime
from functools import cache

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy.types import DateTime, String, TypeDecorator

from app.config import settings

# Every Fernet token starts with this (version byte and timestamp); provider tokens never do
FERNET_PREFIX = "gAAAAA"


@cache
def token_cipher() -> MultiFernet:
    """The cipher for TOKEN_ENCRYPTION_KEY: the first key encrypts, every key decrypts."""
    if not settings.TOKEN_ENCRYPTION_KEYS:
        raise ValueError("TOKEN_ENCRYPTION_KEY is not set")
    try:
        return MultiFernet([Fernet(key) for key in settings.TOKEN_ENCRYPTION_KEYS])
    except ValueError as e:
        raise ValueError(f"TOKEN_ENCRYPTION_KEY is invalid: {e}") from None


class UTCDateTime(TypeDecorator):
    """Stores tz-aware UTC datetimes as naive UTC in SQLite,
    and re-attaches UTC tzinfo when loading back."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"Naive datetime passed to UTCDateTime column: {value!r}")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class EncryptedString(TypeDecorator):
    """Stores strings encrypted with TOKEN_ENCRYPTION_KEY; plain text from before encryption reads as is."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect):
        if value is None:
            return None
        return token_cipher().encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect):
        if value is None or not value.startswith(FERNET_PREFIX):
            return value
        return token_cipher().decrypt(value.encode()).decode()
