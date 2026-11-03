"""The Note model: one written note about one person, in one user's private archive."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NOTE_MAX_LENGTH = 10_000


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        # The ownership guarantee: a note's (person, owner) pair must match a person row with
        # that same owner, so a note can never belong to a different owner than its person,
        # even if application code has a defect. Deleting a person deletes their notes.
        ForeignKeyConstraint(
            ["person_id", "owner_id"],
            ["people.id", "people.owner_id"],
            name="fk_notes_person_id_owner_id_people",
            ondelete="CASCADE",
        ),
        # Backstops for the API validation. `\\S` is "any non-whitespace character".
        CheckConstraint("content ~ '\\S'", name="content_not_blank"),
        CheckConstraint(f"char_length(content) <= {NOTE_MAX_LENGTH}", name="content_max_length"),
        # Serves the list query (one owner's notes for one person, newest first) and the
        # cascade lookup from a deleted person.
        Index(
            "ix_notes_owner_id_person_id_created_at",
            "owner_id",
            "person_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The verified Supabase user id. Set only from the authenticated identity, never exposed.
    owner_id: Mapped[uuid.UUID]
    person_id: Mapped[uuid.UUID]
    content: Mapped[str] = mapped_column(Text)
    # clock_timestamp() is the real time of the write (now() is the start of the transaction),
    # so notes written in quick succession order correctly and an edit always moves updated_at.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
