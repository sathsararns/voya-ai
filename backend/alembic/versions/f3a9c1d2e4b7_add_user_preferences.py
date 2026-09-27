"""add user preferences columns

Revision ID: f3a9c1d2e4b7
Revises: 27a4124e9f89
Create Date: 2026-09-27 00:00:00.000000

Adds two nullable columns to the (Alembic-owned) `users` table for the
persistent, account-scoped travel-preferences summary described in
services/user_preferences.py and db/models.py's User.preferences docstring:

- `preferences`: a small JSON blob (preferred destinations, budget range,
  hotel/transport/food/activity preferences, overall travel style).
- `preferences_updated_at`: when it was last changed, for observability.

Both nullable with no server default — every existing row gets NULL, which
every reader (extract_and_merge_preferences, format_preferences_context)
already treats identically to "no preferences yet" (a brand-new signup gets
the same NULL before their first chat message teaches the app anything).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c1d2e4b7'
down_revision: Union[str, Sequence[str], None] = '27a4124e9f89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("preferences", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("preferences_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "preferences_updated_at")
    op.drop_column("users", "preferences")
