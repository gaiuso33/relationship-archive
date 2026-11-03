"""create notes

Adds the `notes` table for the owner-scoped Notes API (issue #13).

A note references its person through the composite key (person_id, owner_id), so the database
guarantees that a note and its person have the same owner. That key needs a unique constraint
on people (id, owner_id), which is added first.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_people_id_owner_id"), "people", ["id", "owner_id"])
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("content ~ '\\S'", name=op.f("ck_notes_content_not_blank")),
        sa.CheckConstraint(
            "char_length(content) <= 10000", name=op.f("ck_notes_content_max_length")
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "owner_id"],
            ["people.id", "people.owner_id"],
            name=op.f("fk_notes_person_id_owner_id_people"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
    )
    op.create_index(
        op.f("ix_notes_owner_id_person_id_created_at"),
        "notes",
        ["owner_id", "person_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Notes first: the unique constraint cannot be dropped while the notes key depends on it.
    # This deletes all notes (there is no data to preserve them in at revision 0002).
    op.drop_index(op.f("ix_notes_owner_id_person_id_created_at"), table_name="notes")
    op.drop_table("notes")
    op.drop_constraint(op.f("uq_people_id_owner_id"), "people", type_="unique")
