"""drop users.is_verified

Revision ID: 895e6be15246
Revises: 9a5b492497ec
Create Date: 2026-09-24 00:00:00.000000

Signup no longer has an email-verification step (OTP-based verification was
removed in favor of a simple, immediate signup + secure token/link-based
password reset) so the column that tracked verification state has no
remaining reader or writer. See routes/auth.py and the auth writeup for the
full rationale.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '895e6be15246'
down_revision: Union[str, Sequence[str], None] = '9a5b492497ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "is_verified")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
