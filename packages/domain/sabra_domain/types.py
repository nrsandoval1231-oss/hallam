"""Portable SQLAlchemy column types.

The same models must run on SQLite (pilot, tests) and PostgreSQL (deployment), so
timestamps are normalized to UTC-aware values on read regardless of what the
driver handed back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, TypeDecorator


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never return a naive datetime."""

    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UtcDateTime(TypeDecorator):
    """Stores a naive UTC datetime, returns a timezone-aware UTC datetime."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> datetime | None:
        value = as_utc(value)
        if value is None:
            return None
        return value.replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        return as_utc(value)


class JsonBlob(TypeDecorator):
    """JSON payload column.

    `JSON` maps to native JSON/JSONB on PostgreSQL and to TEXT on SQLite, so no
    dialect-specific import is needed and JSON queries remain available on both.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return value
