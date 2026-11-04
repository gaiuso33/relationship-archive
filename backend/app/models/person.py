"""The Person model: one person in one user's private archive."""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (
        CheckConstraint("char_length(btrim(display_name)) > 0", name="display_name_not_blank"),
        # Lets `notes` reference (id, owner_id), so the database itself guarantees that a note
        # has the same owner as its person. `id` is already unique; this is the target the
        # composite foreign key needs.
        UniqueConstraint("id", "owner_id", name="uq_people_id_owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The verified Supabase user id (`sub`). It has no foreign key: users live in Supabase's
    # own database, not in this one. It is set only from the authenticated identity, and is
    # never exposed through the API.
    owner_id: Mapped[uuid.UUID] = mapped_column(index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    birthday: Mapped[date | None] = mapped_column(Date)
    # An IANA time zone name such as "Africa/Lagos", chosen by the user.
    timezone: Mapped[str | None] = mapped_column(String(64))
    # Short, user-entered text entries. Never AI-suggested values (ADR-003).
    interests: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]"), default=list
    )
    preferences: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]"), default=list
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
