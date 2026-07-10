"""add groups and expected_delivery columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("groups", sa.Text()))
    op.add_column("tickets", sa.Column("expected_delivery", sa.String(100)))


def downgrade() -> None:
    op.drop_column("tickets", "expected_delivery")
    op.drop_column("tickets", "groups")
