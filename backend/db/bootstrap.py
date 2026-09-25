"""Lightweight, dependency-free schema bootstrap for the pre-Alembic tables.

This originally covered every table in the project (there was no Alembic).
Now that Alembic manages new schema (see alembic/versions/ — starting with
the `users` table for authentication), this bootstrap is intentionally
scoped to ONLY the tables it already owned before Alembic existed:
conversations and chat_history. It must never touch a table Alembic owns —
Base.metadata.create_all() has no notion of "who owns this table", so left
unscoped it would happily (re-)create `users` outside Alembic's migration
history the moment db/models.py is imported, which is exactly the bug that
motivated pinning BOOTSTRAP_TABLES explicitly below instead of passing
Base.metadata.create_all() the whole registry.

Base.metadata.create_all() only creates tables that don't exist yet — it
never alters a table that's already there, so when a column (like
chat_history.session_id or .conversation_id) gets added to a model after the
database was first created, the live table falls out of sync with the code
and every query against that column fails with psycopg2.errors.UndefinedColumn.

run_schema_bootstrap() closes that gap without a full migration tool:
1. create_all() first, for BOOTSTRAP_TABLES that don't exist at all yet.
2. For tables that DO already exist, compare their live columns against what
   the ORM models declare, and ALTER TABLE ADD COLUMN for anything missing.

New columns are only ever added, never dropped, renamed, or backfilled with
computed values — existing rows are left exactly as they are (missing
columns simply come back NULL), so no data is lost or rewritten. This is
safe for the nullable columns this project currently adds; it is not a
general substitute for real migrations, which is exactly why new tables
(users, and anything added after it) go through Alembic instead.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from db.session import Base

# Table names this bootstrap is allowed to create/sync. Deliberately an
# explicit allowlist, not "everything on Base.metadata" — see module
# docstring for why that distinction matters once Alembic-owned tables
# (like `users`) are registered on the same Base.
BOOTSTRAP_TABLES = {"conversations", "chat_history"}


def run_schema_bootstrap(engine: Engine) -> None:
    # Import models so every table is registered on Base.metadata before we
    # read it below (routes -> db.crud -> db.models already does this at
    # normal app startup, this just makes the dependency explicit here too).
    import db.models  # noqa: F401

    bootstrap_tables = [
        table for table in Base.metadata.sorted_tables if table.name in BOOTSTRAP_TABLES
    ]

    # 1. Create any bootstrap-owned table that doesn't exist yet at all.
    Base.metadata.create_all(bind=engine, tables=bootstrap_tables)

    # 2. Add any column that exists in the models but not in the live table.
    inspector = inspect(engine)

    with engine.begin() as connection:
        for table in bootstrap_tables:
            if not inspector.has_table(table.name):
                continue  # brand new table — create_all() already built it

            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}

            for column in table.columns:
                if column.name in existing_columns:
                    continue

                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(
                    text(
                        f'ALTER TABLE "{table.name}" '
                        f'ADD COLUMN IF NOT EXISTS "{column.name}" {column_type}'
                    )
                )

                if column.index:
                    index_name = f"ix_{table.name}_{column.name}"
                    connection.execute(
                        text(
                            f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                            f'ON "{table.name}" ("{column.name}")'
                        )
                    )
