"""The single source of "now" for values written to the database.

Every `DateTime` column in `models.py` is naive — `TIMESTAMP WITHOUT TIME ZONE` — matching
`server_default=func.now()`. SQLite happily accepted a timezone-AWARE datetime into one of those;
**PostgreSQL rejects it outright**:

    (asyncpg) invalid input for query argument: can't subtract offset-naive and offset-aware
    datetimes  /  column "published_at" is of type timestamp without time zone

That is what broke portal publishing after the Postgres migration (`portal.published_at =
datetime.now(timezone.utc)` → 500). Two helpers with the right behaviour already existed in
`routers/users.py` and `deps.py`, duplicated; the sites that broke were the ones that hand-rolled
`datetime.now(timezone.utc)` instead of using either.

So: **one helper, used for every DateTime column write.** Both older helpers now delegate here so
they cannot drift apart again.

The value is still UTC — only the tzinfo tag is dropped, because that is what the column stores.
For anything leaving the API as JSON, keep using aware datetimes / isoformat; this is strictly for
the DB boundary.
"""
from datetime import datetime, timezone


def naive_utcnow() -> datetime:
    """Current UTC time with tzinfo stripped — safe to assign to any model DateTime column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime | None) -> datetime | None:
    """Normalise an incoming datetime for DB comparison or storage: convert an aware value to UTC
    and drop the tag; pass a naive one through unchanged (it is already assumed UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
