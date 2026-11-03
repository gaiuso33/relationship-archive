"""Schemas for the Notes API.

The limits below are the documented contract (docs/notes-api.md). Request schemas forbid
unknown fields, so a client that sends an `owner_id` or `person_id` gets a validation error
instead of having the value silently ignored: both come only from the verified token and the
URL. Note text is never echoed in a validation error (see `app/core/errors.py`).
"""

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.note import NOTE_MAX_LENGTH

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# PostgreSQL's OFFSET is a 64-bit number and fails on anything larger. This bound keeps an
# absurd value a validation error instead of a database error.
MAX_OFFSET = 2_147_483_647

# Whitespace control characters that are normal in written text.
_ALLOWED_CONTROL = frozenset("\n\r\t")


def _check_content(value: str) -> str:
    """Accept free text; reject blank text, text that is too long, and unsupported characters.

    The text is stored exactly as sent: leading and trailing whitespace, newlines and tabs
    are kept. NUL and other control characters are rejected (PostgreSQL cannot store NUL, and
    the rest are never meaningful in a note), as are lone surrogates, which cannot be encoded.
    """
    if len(value) > NOTE_MAX_LENGTH:
        raise ValueError("is too long")
    if not value.strip():
        raise ValueError("must not be blank")
    for character in value:
        category = unicodedata.category(character)
        if category == "Cs" or (category == "Cc" and character not in _ALLOWED_CONTROL):
            raise ValueError("contains unsupported characters")
    return value


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        return _check_content(value)


class NoteUpdate(NoteCreate):
    """Edit a note: `content` is required, and replaces the existing text."""


class NoteResponse(BaseModel):
    """A note as returned to its owner. It deliberately has no `owner_id`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID
    content: str
    created_at: datetime
    updated_at: datetime


class NoteList(BaseModel):
    items: list[NoteResponse]


NoteId = Annotated[uuid.UUID, Field(description="The note's identifier.")]
