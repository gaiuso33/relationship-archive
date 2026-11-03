"""ORM models.

Importing a model module here registers its tables on `Base.metadata`, which is
what Alembic compares against the database.
"""

from app.models.person import Person

__all__ = ["Person"]
