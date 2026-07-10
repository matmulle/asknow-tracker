"""add group_name and application columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("group_name", sa.Text()))
    op.add_column("tickets", sa.Column("application", sa.Text()))


def downgrade() -> None:
    op.drop_column("tickets", "application")
    op.drop_column("tickets", "group_name")
