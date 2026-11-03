"""Data access for notes. Every function takes the owner and the person, and applies both in SQL.

This is the only module that queries `Note`. There is no function that reads, changes or
deletes a note by its id alone: the owner and the parent person are required arguments of
every one, and every statement carries `owner_id` and `person_id` conditions. Something that
belongs to another user (or to another of the caller's people) is therefore indistinguishable
from something that does not exist: the caller sees `None` or `False`.

The composite foreign key (person_id, owner_id) on `notes` is a backstop that makes a note
with a different owner than its person impossible even if this module had a defect. It is not
the authorization mechanism: `create_note` checks the person's owner in the same statement.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import Text, Uuid, delete, func, insert, literal, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.note import Note
from app.models.person import Person

_FOREIGN_KEY_VIOLATION = "23503"


def _owns_person(session: Session, owner_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    statement = select(Person.id).where(Person.id == person_id, Person.owner_id == owner_id)
    return session.scalar(statement) is not None


def list_notes(
    session: Session, owner_id: uuid.UUID, person_id: uuid.UUID, *, limit: int, offset: int
) -> Sequence[Note] | None:
    """The owner's notes for one person, newest first, or None if the person is not theirs."""
    if not _owns_person(session, owner_id, person_id):
        return None
    statement = (
        select(Note)
        .where(Note.owner_id == owner_id, Note.person_id == person_id)
        .order_by(Note.created_at.desc(), Note.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return session.scalars(statement).all()


def create_note(
    session: Session, owner_id: uuid.UUID, person_id: uuid.UUID, content: str
) -> Note | None:
    """Add a note to the owner's person, or return None if the person is not theirs.

    One INSERT ... SELECT: the row is produced only if a person with this id and this owner
    exists, so the ownership test and the write cannot be separated.
    """
    # One reading of the clock for both columns, so a new note has created_at == updated_at
    # (clients can tell an edited note by updated_at > created_at). Reading the clock twice
    # would give two slightly different values.
    written_at = select(func.clock_timestamp().label("written_at")).subquery()
    owned_person = (
        select(
            literal(uuid.uuid4(), Uuid()),
            Person.owner_id,
            Person.id,
            literal(content, Text()),
            written_at.c.written_at,
            written_at.c.written_at,
        )
        .select_from(Person)
        .join(written_at, true())
        .where(Person.id == person_id, Person.owner_id == owner_id)
    )
    statement = (
        insert(Note)
        .from_select(
            ["id", "owner_id", "person_id", "content", "created_at", "updated_at"], owned_person
        )
        .returning(Note)
    )
    try:
        note = session.scalars(statement).one_or_none()
        session.commit()
    except IntegrityError as error:
        # The person was deleted after the ownership test saw it but before the row was
        # checked. It is the same situation as a person that does not exist.
        if getattr(error.orig, "sqlstate", None) != _FOREIGN_KEY_VIOLATION:
            raise
        session.rollback()
        return None
    return note


def update_note(
    session: Session,
    owner_id: uuid.UUID,
    person_id: uuid.UUID,
    note_id: uuid.UUID,
    content: str,
) -> Note | None:
    """Replace the text of the owner's note, in one statement that includes every test."""
    statement = (
        update(Note)
        .where(Note.id == note_id, Note.person_id == person_id, Note.owner_id == owner_id)
        .values(content=content, updated_at=func.clock_timestamp())
        .returning(Note)
        .execution_options(populate_existing=True)
    )
    note = session.scalars(statement).one_or_none()
    session.commit()
    return note


def delete_note(
    session: Session, owner_id: uuid.UUID, person_id: uuid.UUID, note_id: uuid.UUID
) -> bool:
    statement = delete(Note).where(
        Note.id == note_id, Note.person_id == person_id, Note.owner_id == owner_id
    )
    deleted = session.execute(statement).rowcount > 0
    session.commit()
    return deleted
