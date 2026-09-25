"""add users table for authentication

Revision ID: 9a5b492497ec
Revises:
Create Date: 2026-09-23 14:24:41.391968

Deliberately scoped to only the new `users` table. This project's existing
tables (conversations, chat_history) are still created/synced by
db/bootstrap.py's run_schema_bootstrap() at app startup, not by Alembic —
retrofitting them into Alembic's migration history is a separate, larger
change (would need `alembic stamp` on already-provisioned databases to avoid
Alembic trying to re-create tables that already exist) and is intentionally
left as a follow-up rather than bundled into the auth feature. See the auth
writeup's "limitations" section.

Because of that, `alembic revision --autogenerate` run after this migration
will likely propose ALSO creating conversations/chat_history (Alembic has no
record of them). Until the follow-up above happens, write new migrations by
hand instead of trusting autogenerate's diff for those two tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a5b492497ec'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
