"""rename requested_for to requested_by

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tickets", "requested_for", new_column_name="requested_by")


def downgrade() -> None:
    op.alter_column("tickets", "requested_by", new_column_name="requested_for")
