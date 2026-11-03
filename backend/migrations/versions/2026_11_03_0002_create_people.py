"""create people

Adds the `people` table for the owner-scoped People API (issue #12).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column(
            "interests",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "preferences",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) > 0", name=op.f("ck_people_display_name_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
    )
    op.create_index(op.f("ix_people_owner_id"), "people", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_people_owner_id"), table_name="people")
    op.drop_table("people")
