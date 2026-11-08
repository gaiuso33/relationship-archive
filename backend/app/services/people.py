"""Data access for people. Every function takes the owner and applies it in SQL.

This is the only module that queries `Person`. There is deliberately no function that reads,
changes or deletes a person by id alone: the owner is a required argument of every one, and
every statement carries `Person.owner_id == owner_id`. A person who belongs to someone else is
therefore indistinguishable from one that does not exist (the caller sees `None`/`False`), so
an identifier from another user can neither be used nor confirmed to exist.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.person import Person
from app.schemas.people import PersonCreate


def list_people(session: Session, owner_id: uuid.UUID) -> Sequence[Person]:
    statement = (
        select(Person)
        .where(Person.owner_id == owner_id)
        .order_by(func.lower(Person.display_name), Person.id)
    )
    return session.scalars(statement).all()


def search_people(session: Session, owner_id: uuid.UUID, query: str) -> Sequence[Person]:
    """The owner's people whose name contains `query`, in the same order as `list_people`.

    Names only: notes, interests and preferences are never searched (product requirements,
    section 5.2). `query` is a bound parameter, never part of the SQL text, and is escaped
    (`autoescape`) so that `%`, `_` and `/` in it match themselves instead of acting as LIKE
    wildcards or the escape character. The statement is `name ILIKE '%' || :query || '%'
    ESCAPE '/'`, so a backslash is an ordinary character.

    Case-insensitivity is PostgreSQL's `ILIKE`, which follows the database's character-class
    settings (`LC_CTYPE`, or the ICU locale). In a `C` database only ASCII letters fold; in
    others most scripts fold, with differences for some letters (docs/people-api.md). There is
    no accent folding and no Unicode normalisation. The owner filter is in the same statement.
    """
    statement = (
        select(Person)
        .where(Person.owner_id == owner_id, Person.display_name.icontains(query, autoescape=True))
        .order_by(func.lower(Person.display_name), Person.id)
    )
    return session.scalars(statement).all()


def get_person(session: Session, owner_id: uuid.UUID, person_id: uuid.UUID) -> Person | None:
    statement = select(Person).where(Person.id == person_id, Person.owner_id == owner_id)
    return session.scalars(statement).one_or_none()


def create_person(session: Session, owner_id: uuid.UUID, data: PersonCreate) -> Person:
    person = Person(owner_id=owner_id, **data.model_dump())
    session.add(person)
    session.commit()
    session.refresh(person)
    return person


def update_person(
    session: Session, owner_id: uuid.UUID, person_id: uuid.UUID, changes: dict[str, object]
) -> Person | None:
    """Apply `changes` to the owner's person, in one statement that includes the ownership test."""
    statement = (
        update(Person)
        .where(Person.id == person_id, Person.owner_id == owner_id)
        .values(**changes, updated_at=func.now())
        .returning(Person)
        .execution_options(populate_existing=True)
    )
    person = session.scalars(statement).one_or_none()
    session.commit()
    return person


def delete_person(session: Session, owner_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    statement = delete(Person).where(Person.id == person_id, Person.owner_id == owner_id)
    deleted = session.execute(statement).rowcount > 0
    session.commit()
    return deleted
