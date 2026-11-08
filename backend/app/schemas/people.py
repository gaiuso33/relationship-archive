"""Schemas for the People API.

The limits below are the documented contract (docs/people-api.md). Request schemas forbid
unknown fields, so a client that sends an `owner_id` (or anything else we do not define) gets
a validation error instead of having the value silently ignored. The owner is never part of
a request or a response: it comes only from the verified token.
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DISPLAY_NAME_MAX_LENGTH = 120
ENTRY_MAX_LENGTH = 100
MAX_ENTRIES = 50
TIMEZONE_MAX_LENGTH = 64
EARLIEST_BIRTHDAY = date(1900, 1, 1)

# IANA names such as "Africa/Lagos" or "America/Argentina/Buenos_Aires". The shape is checked,
# not membership of the time zone database, which depends on the host.
_TIMEZONE_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*$")


def _clean_text(value: str, *, max_length: int) -> str:
    """Trim, then require 1 to `max_length` printable characters (no control characters)."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be empty")
    if len(cleaned) > max_length:
        raise ValueError("is too long")
    if not cleaned.isprintable():
        raise ValueError("contains control characters")
    return cleaned


def _clean_entries(values: list[str]) -> list[str]:
    """Trim each entry, reject empty or oversized ones, and drop case-insensitive duplicates.

    The first spelling of a duplicate is kept and the order is preserved.
    """
    if len(values) > MAX_ENTRIES:
        raise ValueError("has too many entries")
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        entry = _clean_text(value, max_length=ENTRY_MAX_LENGTH)
        key = entry.casefold()
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def _check_birthday(value: date) -> date:
    # One day of slack so that a user ahead of UTC can still enter today's date.
    if value < EARLIEST_BIRTHDAY or value > datetime.now(UTC).date() + timedelta(days=1):
        raise ValueError("is out of range")
    return value


def _check_timezone(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > TIMEZONE_MAX_LENGTH or not _TIMEZONE_SHAPE.match(cleaned):
        raise ValueError("is not a time zone name")
    return cleaned


class _PersonFields(BaseModel):
    """Validators shared by create and update."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", check_fields=False)
    @classmethod
    def _display_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("must not be null")
        return _clean_text(value, max_length=DISPLAY_NAME_MAX_LENGTH)

    @field_validator("birthday", check_fields=False)
    @classmethod
    def _birthday(cls, value: date | None) -> date | None:
        return None if value is None else _check_birthday(value)

    @field_validator("timezone", check_fields=False)
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        return None if value is None else _check_timezone(value)

    @field_validator("interests", "preferences", check_fields=False)
    @classmethod
    def _entries(cls, value: list[str] | None) -> list[str]:
        if value is None:
            raise ValueError("must not be null")
        return _clean_entries(value)


class PersonCreate(_PersonFields):
    display_name: str
    birthday: date | None = None
    timezone: str | None = None
    interests: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)


class PersonUpdate(_PersonFields):
    """A partial update: only the fields that are sent change.

    `display_name`, `interests` and `preferences` cannot be null (send `[]` to clear a list).
    `birthday` and `timezone` can be null to clear them.
    """

    display_name: str | None = None
    birthday: date | None = None
    timezone: str | None = None
    interests: list[str] | None = None
    preferences: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "PersonUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self

    def changes(self) -> dict[str, object]:
        """The fields the client sent, and only those."""
        return self.model_dump(include=self.model_fields_set)


class PersonResponse(BaseModel):
    """A person as returned to their owner. It deliberately has no `owner_id`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    birthday: date | None
    timezone: str | None
    interests: list[str]
    preferences: list[str]
    created_at: datetime
    updated_at: datetime


class PersonList(BaseModel):
    items: list[PersonResponse]


class PersonSearch(BaseModel):
    """A search by person name (issue #15). The text is sent in the body, never in the URL.

    The text is trimmed and must be 1 to 120 printable characters, the same rules as a name, so
    a query can never contain something a name cannot. It is matched literally (see
    `app.services.people.search_people`); the stored name is never changed by a search.
    """

    model_config = ConfigDict(extra="forbid")

    query: str

    @field_validator("query")
    @classmethod
    def _query(cls, value: str) -> str:
        return _clean_text(value, max_length=DISPLAY_NAME_MAX_LENGTH)


PersonId = Annotated[uuid.UUID, Field(description="The person's identifier.")]
