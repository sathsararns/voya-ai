"""conversations.user_id foreign key

Revision ID: 27a4124e9f89
Revises: 895e6be15246
Create Date: 2026-09-24 00:00:00.000000

conversations is a bootstrap-owned table, not an Alembic-owned one (see
db/bootstrap.py's module docstring) — the user_id COLUMN itself is added by
db/bootstrap.py's run_schema_bootstrap() the first time the app starts after
db/models.py declares Conversation.user_id. Bootstrap only ever does
ADD COLUMN / CREATE INDEX though, never constraints, so the foreign key
itself needs a real migration.

This is written defensively (checked with an inspector, not assumed) because
of that split ownership: on a database where the app hasn't been started yet
since this change landed, `conversations` may not exist at all, or may exist
without `user_id` yet — either way this is a no-op here, and the constraint
gets picked up the next time this migration runs after the app has bootstrapped
the column. Safe to run before or after that point, and safe to run more than
once.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '27a4124e9f89'
down_revision: Union[str, Sequence[str], None] = '895e6be15246'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "fk_conversations_user_id_users"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "conversations" not in inspector.get_table_names():
        return  # not created yet — bootstrap will create it with user_id already included

    columns = {c["name"] for c in inspector.get_columns("conversations")}
    if "user_id" not in columns:
        return  # bootstrap hasn't added the column on this database yet

    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("conversations")}
    if FK_NAME in existing_fks:
        return  # already applied

    op.create_foreign_key(FK_NAME, "conversations", "users", ["user_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "conversations" not in inspector.get_table_names():
        return

    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("conversations")}
    if FK_NAME in existing_fks:
        op.drop_constraint(FK_NAME, "conversations", type_="foreignkey")
