import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy.types import DateTime, TypeDecorator
from sqlalchemy.types import Enum as SQLAlchemyEnum


def new_id() -> str:
    """The primary key for a new record; uuid7 sorts by creation time."""
    return str(uuid.uuid7())


def enum_type(enum_class: type[Enum]) -> SQLAlchemyEnum:
    """The member names in a string column with a check constraint: adding a member only replaces the constraint."""
    return SQLAlchemyEnum(enum_class, native_enum=False, create_constraint=True, length=32)


class UTCDateTime(TypeDecorator):
    """Stores tz-aware datetimes as naive UTC, and re-attaches UTC tzinfo when loading back."""

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
