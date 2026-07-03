from datetime import UTC, datetime

from sqlalchemy.types import DateTime, TypeDecorator


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
