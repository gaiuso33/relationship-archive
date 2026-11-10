"""Report the database's locale and collation settings (docs/database-collation-checklist.md).

Run it against the database named by DATABASE_URL:

    python -m app.tools.db_collation              # plain text
    python -m app.tools.db_collation --markdown   # a table, for the CI job summary

Search matches names with PostgreSQL's ILIKE, whose case-insensitivity follows the
database's locale (docs/people-api.md, "Searching by name"). This tool only reports; it
never changes anything and never fails because of a value it finds. It prints fixed facts
from the PostgreSQL catalog, never the database URL, a password, a host or any row.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from app.core.config import get_settings
from app.db.session import create_db_engine

# `to_jsonb(d)->>...` reads columns by name, so one statement works on every supported
# version: the ICU locale column is `daticulocale` in PostgreSQL 15 and 16 and `datlocale`
# from 17 (which also adds the `builtin` provider). `datlocprovider` needs PostgreSQL 15+.
DATABASE_SQL = """
select split_part(version(), ' ', 2) as server,
       pg_encoding_to_char(encoding) as encoding,
       datlocprovider,
       datcollate,
       datctype,
       to_jsonb(d)->>'datlocale' as datlocale,
       to_jsonb(d)->>'daticulocale' as daticulocale,
       datcollversion
from pg_database d
where datname = current_database()
"""

COLUMN_SQL = """
select collation_name
from information_schema.columns
where table_schema = current_schema() and table_name = 'people' and column_name = 'display_name'
"""

PROVIDERS = {"c": "libc", "i": "icu", "b": "builtin"}


def collect(connection: Connection) -> dict[str, str | None]:
    """Return the settings as plain strings (None where PostgreSQL has no value)."""
    row = connection.execute(text(DATABASE_SQL)).one()
    info: dict[str, str | None] = dict(row._mapping)
    info["provider"] = PROVIDERS.get(str(info["datlocprovider"]), str(info["datlocprovider"]))
    column = connection.execute(text(COLUMN_SQL)).first()
    # No row: the table does not exist yet. A row with None: the column uses the database default.
    info["people_display_name_collation"] = (
        "(people table not found)" if column is None else (column[0] or "(database default)")
    )
    return info


FIELDS: tuple[tuple[str, str], ...] = (
    ("server", "PostgreSQL version"),
    ("encoding", "Encoding"),
    ("provider", "Locale provider"),
    ("datcollate", "datcollate"),
    ("datctype", "datctype"),
    ("datlocale", "datlocale (PostgreSQL 17+, ICU and builtin)"),
    ("daticulocale", "daticulocale (PostgreSQL 15 and 16, ICU)"),
    ("datcollversion", "Collation version"),
    ("people_display_name_collation", "Collation of people.display_name"),
)


def render(info: dict[str, str | None], *, markdown: bool) -> str:
    values = [
        (label, "-" if info.get(key) in (None, "") else str(info[key])) for key, label in FIELDS
    ]
    if markdown:
        lines = [
            "### Database collation (recorded, not enforced)",
            "",
            "| Setting | Value |",
            "|---|---|",
        ]
        lines += [f"| {label} | `{value}` |" for label, value in values]
        return "\n".join(lines) + "\n"
    width = max(len(label) for label, _ in values)
    return "\n".join(f"{label.ljust(width)}  {value}" for label, value in values) + "\n"


def report(engine: Engine, *, markdown: bool) -> str:
    with engine.connect() as connection:
        return render(collect(connection), markdown=markdown)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report the database locale and collation.")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown table")
    args = parser.parse_args(argv)

    url = get_settings().database_url
    if url is None:
        print("DATABASE_URL is not set. See docs/development.md.", file=sys.stderr)
        return 2
    engine = create_db_engine(url.get_secret_value())
    try:
        print(report(engine, markdown=args.markdown), end="")
    except ProgrammingError as exc:
        # An undefined column: the server is older than PostgreSQL 15 (no datlocprovider).
        print(
            f"Could not read the database settings ({type(exc.orig).__name__}). "
            "This tool needs PostgreSQL 15 or newer.",
            file=sys.stderr,
        )
        return 1
    except SQLAlchemyError as exc:
        print(f"Could not query the database ({type(exc).__name__}).", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
